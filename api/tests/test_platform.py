"""Rate limits, migrations, API contract, settings, dashboard and misc endpoints."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from conftest import OWNER_ADMIN_URL, PASSWORD, Api, create_task, new_email, recreate_database, run_migrations
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import schemas as S

# ------------------------------------------------------------------ rate limiting


def test_login_rate_limited_per_email(app: Any, make_user: Any, owner_engine: Any) -> None:
    user = make_user()
    with owner_engine.begin() as c:  # forget make_user's own successful login
        c.execute(text("DELETE FROM rate_limit_buckets"))
    codes = []
    for i in range(12):
        api = Api(app, ip=f"192.0.2.{i + 1}")  # different IPs: the per-email limit applies
        codes.append(api.post("/auth/login", {"email": user.email, "password": "wrong-password-1"}).status_code)
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    # Even the right password is refused while limited.
    r = Api(app, ip="192.0.2.200").post("/auth/login", {"email": user.email, "password": PASSWORD})
    assert r.status_code == 429 and "retry-after" in r.headers
    assert r.json()["error"]["code"] == "rate_limited"


def test_register_rate_limited_per_ip(app: Any) -> None:
    api = Api(app, ip="198.51.100.7")
    codes = [
        api.post(
            "/auth/register", {"name": "N", "email": new_email(), "password": PASSWORD, "accept_terms": True}
        ).status_code
        for _ in range(6)
    ]
    assert codes == [202] * 5 + [429]


def test_forgot_password_rate_limited(app: Any) -> None:
    email = new_email()
    codes = [Api(app).post("/auth/forgot-password", {"email": email}).status_code for _ in range(4)]
    assert codes == [202, 202, 202, 429]


def test_task_creation_rate_limited(make_user: Any) -> None:
    api = make_user()
    codes = [
        api.post("/tasks", {"prompt": f"Research item number {i} in detail", "type": "research"}).status_code
        for i in range(31)
    ]
    assert codes[:30] == [201] * 30 and codes[30] == 429


# ------------------------------------------------------------------ migrations


def test_migrations_upgrade_downgrade_upgrade() -> None:
    name = "steward_migrations_test"
    recreate_database(name)
    url = make_url(OWNER_ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    run_migrations(url)
    run_migrations(url, "base", downgrade=True)
    run_migrations(url)
    eng = create_engine(url)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0007"
        tables = set(c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars())
    eng.dispose()
    assert {"users", "workspaces", "tasks", "audit_events", "deletion_requests", "jobs", "usage_counters"} <= tables


def test_models_match_migrations(owner_engine: Any) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.core.db import Base

    with owner_engine.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert diff == [], diff


# ------------------------------------------------------------------ API contract


def test_error_envelope_and_request_id(app: Any) -> None:
    api = Api(app)
    r = api.c.get("/api/v1/me", headers={"X-Request-ID": "trace-abc-12345"})
    assert r.status_code == 401
    body = r.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message", "request_id", "details"}
    assert body["error"]["request_id"] == "trace-abc-12345" == r.headers["x-request-id"]
    r = api.get("/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert len(r.headers["x-request-id"]) >= 8
    for h in ("x-content-type-options", "x-frame-options", "content-security-policy", "cache-control"):
        assert h in r.headers


def test_oversized_request_rejected(app: Any, monkeypatch: Any) -> None:
    api = Api(app)
    r = api.c.post(
        "/api/v1/auth/login",
        content=b"x" * (31 * 1024 * 1024),
        headers={"X-CSRF-Token": api.csrf, "Content-Type": "application/json"},
    )
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


TS_TYPES = Path(__file__).resolve().parents[2] / "web" / "lib" / "api" / "types.ts"


def _ts_fields(name: str) -> set[str]:
    src = TS_TYPES.read_text()
    m = re.search(r"export interface " + name + r"(?: extends (\w+))?\s*\{(.*?)\n\}", src, re.S)
    assert m, name
    fields = set(re.findall(r"^\s{2}(\w+)\??:", m.group(2), re.M))
    if m.group(1):
        fields |= _ts_fields(m.group(1))
    return fields


@pytest.mark.parametrize(
    ("ts", "model"),
    [
        ("User", S.UserOut),
        ("Me", S.MeOut),
        ("TaskSummary", S.TaskSummaryOut),
        ("Task", S.TaskOut),
        ("TaskStep", S.TaskStepOut),
        ("TaskEvent", S.TaskEventOut),
        ("Approval", S.ApprovalOut),
        ("FileItem", S.FileOut),
        ("Automation", S.AutomationOut),
        ("Integration", S.IntegrationOut),
        ("ActivityEvent", S.ActivityOut),
        ("NotificationItem", S.NotificationOut),
        ("Dashboard", S.DashboardOut),
        ("SecurityStatus", S.SecurityStatusOut),
        ("Settings", S.SettingsOut),
        ("Usage", S.UsageOut),
        ("UploadLimits", S.UploadLimitsOut),
        ("Artifact", S.ArtifactOut),
        ("AccountRequest", S.AccountRequestOut),
        ("CreateTaskInput", S.CreateTaskIn),
        ("DecisionInput", S.DecisionIn),
        ("CreateAutomationInput", S.CreateAutomationIn),
    ],
)
def test_backend_schemas_match_frontend_types(ts: str, model: Any) -> None:
    assert _ts_fields(ts) == set(model.model_fields), f"{ts} differs from {model.__name__}"


def test_openapi_lists_documented_endpoints(app: Any) -> None:
    paths = Api(app).c.get("/api/v1/openapi.json").json()["paths"]
    contract = (Path(__file__).resolve().parents[2] / "API_CONTRACT.md").read_text()
    documented = set(re.findall(r"`(?:GET|POST|PATCH|PUT|DELETE) (/[^`\s]*)`", contract))
    assert documented, "API_CONTRACT.md lists no endpoints"
    for p in documented:
        assert "/api/v1" + p in paths, f"{p} documented but not implemented"
    for p in paths:
        assert p.removeprefix("/api/v1") in documented, f"{p} implemented but not documented"


# ------------------------------------------------------------------ dashboard, settings, misc


def test_dashboard_reflects_real_data(make_user: Any) -> None:
    api = make_user()
    d = api.get("/dashboard").json()
    assert d["counts"] == {
        "in_progress": 0,
        "awaiting_approval_plans": 0,
        "pending_actions": 0,
        "completed_week": 0,
        "needs_attention": 0,
    }
    # The test environment configures no model: the agent says so instead of pretending to work.
    assert d["agent"]["status"] == "not_configured" and d["agent"]["planner"] == "not_configured"
    assert d["agent"]["provider"]["configured"] is False and "Nothing is sent" in d["agent"]["message"]
    assert d["setup"] == {"verify_email": True, "privacy": False, "integration": False, "first_task": False}
    assert d["unread_notifications"] == 1  # welcome note
    create_task(api)
    d = api.get("/dashboard").json()
    assert d["counts"]["in_progress"] == 1 and d["setup"]["first_task"] is True and len(d["recent_tasks"]) == 1


def test_settings_preferences_and_validation(make_user: Any) -> None:
    api = make_user()
    s = api.get("/settings").json()
    assert s["enforced"] is True and s["workspace"]["can_edit"] is True and s["workspace"]["privacy_mode"] == "strict"
    r = api.patch("/me/preferences", {"name": "New Name", "timezone": "Asia/Dhaka", "notifications": {"digest": True}})
    assert (
        r.status_code == 200
        and r.json()["profile"]["name"] == "New Name"
        and r.json()["notifications"]["digest"] is True
    )
    assert api.patch("/me/preferences", {"timezone": "Mars/Base"}).status_code == 422
    r = api.patch(
        "/workspace/settings",
        {"allowed_domains": ["Example.com", "example.com", " docs.example.org "], "ai_rules": {"del": False}},
    )
    assert r.status_code == 200
    assert r.json()["workspace"]["allowed_domains"] == ["example.com", "docs.example.org"]
    assert r.json()["workspace"]["ai_rules"]["del"] is False
    assert api.patch("/workspace/settings", {"allowed_domains": ["not a domain"]}).status_code == 422
    assert api.patch("/workspace/settings", {"privacy_mode": "anything"}).status_code == 422
    assert api.get("/dashboard").json()["setup"]["privacy"] is False
    api.patch("/workspace/settings", {"privacy_mode": "strict"})
    assert api.get("/dashboard").json()["setup"]["privacy"] is True


def test_member_role_cannot_change_workspace_settings(make_user: Any, owner_engine: Any) -> None:
    api = make_user()
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE workspace_memberships SET role = 'member' WHERE user_id = :u"), {"u": api.me["user"]["id"]}
        )
    r = api.patch("/workspace/settings", {"name": "Hijacked"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert api.get("/settings").json()["workspace"]["can_edit"] is False


def test_onboarding_saves_preferences(make_user: Any) -> None:
    api = make_user()
    r = api.post(
        "/me/onboarding",
        {
            "goals": ["wordpress", "research"],
            "where": ["workspace_files"],
            "ai_rules": {"share": True, "publish": True, "prod": True, "del": True, "folders": False},
        },
    )
    assert r.status_code == 200 and r.json()["user"]["onboarding_completed"] is True
    assert api.get("/settings").json()["workspace"]["ai_rules"]["folders"] is False
    assert api.post("/me/onboarding", {"goals": ["Bad Label!"], "where": [], "ai_rules": {}}).status_code == 422


def test_automations_schedule_and_toggle(make_user: Any) -> None:
    api = make_user()
    base = {
        "template": "SEO report",
        "tools": ["Agent analysis"],
        "file_scope": "No files",
        "approval_policy": "Ask before any change",
    }
    sched = api.post(
        "/automations",
        {"name": "Weekly", "trigger": "schedule", "frequency": "weekly", "day": "Monday", "time": "08:00", **base},
    ).json()
    assert sched["enabled"] is True and sched["schedule_text"] == "Every Monday at 08:00" and sched["ref"] == "AU-1"
    assert "creates a plan for review" in sched["next_run_text"]
    r = api.patch(f"/automations/{sched['id']}", {"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["next_run_text"] == "Paused"
    r = api.patch(f"/automations/{sched['id']}", {"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    manual = api.post("/automations", {"name": "Manual", "trigger": "manual", **base}).json()
    assert manual["enabled"] is True
    run = api.post(f"/automations/{manual['id']}/run")
    assert run.status_code == 201 and run.json()["status"] == "queued"
    assert (
        api.post("/automations", {"name": "Bad", "trigger": "manual", **{**base, "template": "Hack"}}).status_code
        == 422
    )


def test_integrations_only_real_adapters_are_connectable(make_user: Any, owner_engine: Any) -> None:
    api = make_user()
    items = {i["key"]: i for i in api.get("/integrations").json()}
    assert set(items) == {"wordpress", "notion", "cloud_storage", "email", "browser", "ai_provider"}
    # Only WordPress (read-only) is a real adapter; nothing is shown as connected unless it is.
    assert [k for k, i in items.items() if i["connectable"]] == ["wordpress"]
    assert items["wordpress"]["operational"] is True and items["notion"]["operational"] is False
    assert all(i["status"] in ("not_connected", "backend_required") for i in items.values())
    for key in ("notion", "email", "cloud_storage", "ai_provider"):
        r = api.post(f"/integrations/{key}/connect")
        assert r.status_code == 409 and r.json()["error"]["code"] == "integration_not_available"
    assert api.post("/integrations/unknown/connect").status_code == 404
    # WordPress: https only, the domain must be allowlisted first, and it is verified by a worker.
    assert api.post("/integrations/wordpress/connect", {"site_url": "http://example.com"}).status_code == 422
    r = api.post("/integrations/wordpress/connect", {"site_url": "https://blog.example.com"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "domain_not_allowed"
    api.patch("/workspace/settings", {"allowed_domains": ["example.com"]})
    r = api.post("/integrations/wordpress/connect", {"site_url": "https://blog.example.com/"})
    assert r.status_code == 202
    assert r.json()["status"] == "not_connected" and r.json()["site_url"] == "https://blog.example.com"
    with owner_engine.connect() as c:
        kinds = c.execute(
            text("SELECT kind, queue FROM jobs WHERE workspace_id = :w AND kind = 'integration.verify'"),
            {"w": api.me["workspace"]["id"]},
        ).all()
    assert [(k.kind, k.queue) for k in kinds] == [("integration.verify", "tools")]


def test_security_status_is_honest(make_user: Any) -> None:
    api = make_user()
    s = api.get("/security/status").json()
    assert s["privacy_enforced"] is True
    checks = {c["key"]: c["status"] for c in s["checklist"]}
    assert (
        checks["csrf"] == "enforced"
        and checks["tenant_isolation"] == "enforced"
        and checks["rate_limits"] == "enforced"
    )
    assert checks["malware_scan"] == "not_configured" and checks["backups"] == "not_configured"
    assert checks["egress"] == "enforced" and checks["deletion"] == "enforced" and checks["policy_engine"] == "enforced"
    assert (
        checks["secrets"] == "enforced"
        and checks["mfa"] == "partial"
        and checks["audit_log"] == "partial"
        and checks["sandbox"] == "partial"
    )
    assert "No AI model is configured" in s["ai_provider_policy"]
    assert checks["session_cookies"] == "partial"  # no Secure flag outside production


def test_notifications_read_flow(make_user: Any) -> None:
    api = make_user()
    n = api.get("/notifications").json()
    assert n["unread"] == 1 and n["items"][0]["target"]["route"].startswith("/")
    assert api.post(f"/notifications/{n['items'][0]['id']}/read").status_code == 204
    assert api.get("/notifications", params={"unread": "true"}).json()["items"] == []
    assert api.post(f"/notifications/{uuid.uuid4()}/read").status_code == 404
    assert api.post("/notifications/read-all").status_code == 204


def test_account_requests_are_recorded_not_executed(make_user: Any) -> None:
    api = make_user()
    r = api.post("/account/deletion-request", {"reason": "Leaving"})
    assert r.status_code == 201 and r.json()["status"] == "received" and "nothing is deleted" in r.json()["message"]
    assert api.get("/me").status_code == 200  # account still exists
    assert api.post("/account/export-request").status_code == 201


def test_production_config_guards(monkeypatch: Any) -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    good: dict[str, Any] = {
        "env": "production",
        "app_origin": "https://app.example.com",
        "mail_backend": "smtp",
        "smtp_host": "smtp.example.com",
        "trust_proxy_headers": True,
        "cookie_secure": False,
        "database_url": "postgresql+psycopg://a:Str0ng-Random-Value@db/x",
        "approval_signing_key": "k" * 40,
        "secrets_encryption_key": "cmFuZG9tLWtleS1mb3ItdGVzdHMtMzItYnl0ZXMhISE=",
        "integration_secrets_public_key": "cHVibGljLWtleS1mb3ItdGVzdHMtMzItYnl0ZXMhISE=",
    }
    s = Settings(**good)
    assert s.cookie_secure is True
    bad_cases = [
        ({"mail_backend": "outbox"}, "MAIL_BACKEND"),
        ({"mail_backend": "disabled"}, "MAIL_BACKEND"),
        ({"smtp_starttls": False}, "STARTTLS"),
        ({"app_origin": "http://app.example.com"}, "https"),
        ({"database_url": "postgresql+psycopg://a:replace-me@db/x"}, "DATABASE_URL"),
        ({"database_url": "postgresql+psycopg://steward_app:devapp@db/x"}, "DATABASE_URL"),
        ({"approval_signing_key": "dev-only-approval-signing-key-change-me"}, "APPROVAL_SIGNING_KEY"),
        ({"approval_signing_key": "short"}, "APPROVAL_SIGNING_KEY"),
        ({"trust_proxy_headers": False}, "TRUST_PROXY_HEADERS"),
        ({"rate_limit_enabled": False}, "rate limiting"),
    ]
    for override, needle in bad_cases:
        with pytest.raises(ValidationError, match=needle):
            Settings(**{**good, **override})


def test_worker_role_skips_http_only_guards_but_keeps_the_rest(monkeypatch: Any) -> None:
    """Workers neither serve HTTP nor send mail; they must still refuse weak secrets and unsafe egress."""
    from pydantic import ValidationError

    from app.core.config import Settings

    worker: dict[str, Any] = {
        "env": "production",
        "process_role": "worker",
        "mail_backend": "disabled",
        "trust_proxy_headers": False,
        "app_origin": "http://localhost:3000",
        "database_url": "postgresql+psycopg://steward_worker:Str0ng-Random-Value@db/x",
        "worker_database_url": "postgresql+psycopg://steward_worker:Str0ng-Random-Value@db/x",
        "approval_signing_key": "k" * 40,
    }
    assert Settings(**worker).process_role == "worker"
    for override, needle in [
        ({"approval_signing_key": "short"}, "APPROVAL_SIGNING_KEY"),
        ({"worker_database_url": "postgresql+psycopg://steward_worker:change-me@db/x"}, "WORKER_DATABASE_URL"),
        ({"egress_allow_http": True}, "EGRESS"),
        ({"model_provider": "mock"}, "MODEL_PROVIDER"),
    ]:
        with pytest.raises(ValidationError, match=needle):
            Settings(**{**worker, **override})
    # The same settings without the worker role are refused (default role is the strict "api").
    with pytest.raises(ValidationError, match="MAIL_BACKEND"):
        Settings(**{k: v for k, v in worker.items() if k != "process_role"})


def test_api_refuses_to_start_with_the_worker_role(monkeypatch: Any) -> None:
    from app import main
    from app.core import config

    monkeypatch.setenv("PROCESS_ROLE", "worker")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="PROCESS_ROLE=worker cannot serve HTTP"):
            main.create_app()
    finally:
        monkeypatch.delenv("PROCESS_ROLE")
        config.get_settings.cache_clear()


def test_migrate_role_needs_no_encryption_keys() -> None:
    from app.core.config import Settings

    Settings(
        env="production",
        process_role="migrate",
        mail_backend="disabled",
        database_url="postgresql+psycopg://steward_app:Str0ng-Random-Value@db/x",
        approval_signing_key="k" * 40,
    )
