"""Authorization matrix over EVERY documented route (Phase 4).

For each route in the OpenAPI document we assert the negative cases:
- anonymous callers are refused (401) except on the explicit public list;
- a user from another workspace gets 404 (no existence oracle) for every ID-taking route,
  and the victim's data is unchanged afterwards (BOLA/IDOR);
- viewers cannot use any write route except their own preferences/notifications/account;
- members cannot use owner/admin-only routes.
A new route fails this test until it is classified here.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from tests.conftest import Api, create_task, upload

PUBLIC = {
    ("GET", "/auth/csrf"),
    ("POST", "/auth/register"),
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
    ("POST", "/auth/verify-email"),
    ("POST", "/auth/resend-verification"),
    ("POST", "/auth/forgot-password"),
    ("POST", "/auth/reset-password"),
    ("POST", "/auth/mfa/verify"),  # second sign-in step: needs the challenge cookie, not a session
}

# Write routes a viewer may still use (they only touch the viewer's own account).
VIEWER_ALLOWED = {
    ("POST", "/me/onboarding"),
    ("PATCH", "/me/preferences"),
    ("POST", "/notifications/read-all"),
    ("POST", "/notifications/{notification_id}/read"),
    ("POST", "/account/export-request"),
    ("POST", "/account/deletion-request"),
    ("POST", "/me/mfa/setup"),
    ("POST", "/me/mfa/enable"),
    ("POST", "/me/mfa/disable"),
    ("POST", "/me/mfa/recovery-codes"),
}

# Owner/admin-only write routes (a member must get 403).
ADMIN_ONLY = {
    ("POST", "/tasks/{task_id}/plan/approve"),
    ("POST", "/tasks/{task_id}/plan/reject"),
    ("POST", "/approvals/{approval_id}/decision"),
    ("PATCH", "/files/{file_id}"),
    ("POST", "/files/{file_id}/restore"),
    ("POST", "/automations"),
    ("PATCH", "/automations/{automation_id}"),
    ("POST", "/automations/{automation_id}/run"),
    ("POST", "/integrations/wordpress/connect"),
    ("POST", "/integrations/{key}/connect"),
    ("POST", "/integrations/{key}/disconnect"),
    ("PATCH", "/workspace/settings"),
}

BODIES: dict[tuple[str, str], Any] = {
    ("POST", "/tasks"): {"prompt": "Write a short report about our launch", "type": "research"},
    ("POST", "/tasks/{task_id}/cancel"): {"reason": "no"},
    ("POST", "/tasks/{task_id}/messages"): {"text": "hello there", "kind": "question"},
    ("POST", "/tasks/{task_id}/plan/approve"): {"selected_step_ids": [str(uuid.uuid4())]},
    ("POST", "/tasks/{task_id}/plan/reject"): {"note": "no"},
    ("POST", "/approvals/{approval_id}/decision"): {"decision": "reject", "acknowledged": True},
    ("PATCH", "/files/{file_id}"): {"collection": "Research"},
    ("POST", "/files/{file_id}/deletion-request"): {"reason": "cleanup please"},
    ("POST", "/automations"): {
        "name": "Weekly",
        "trigger": "manual",
        "template": "SEO report",
        "tools": ["Agent analysis"],
        "file_scope": "No files",
        "approval_policy": "Ask before any change",
    },
    ("PATCH", "/automations/{automation_id}"): {"enabled": False},
    ("POST", "/integrations/wordpress/connect"): {"site_url": "https://blog.example.com"},
    ("PATCH", "/workspace/settings"): {"name": "Renamed"},
    ("PATCH", "/me/preferences"): {"timezone": "UTC"},
    ("POST", "/me/onboarding"): {"goals": [], "where": [], "ai_rules": {}},
    ("POST", "/account/deletion-request"): {"reason": "testing"},
}


def _routes(app: Any) -> list[tuple[str, str]]:
    spec = Api(app).c.get("/api/v1/openapi.json").json()
    out = []
    for path, ops in spec["paths"].items():
        for method in ops:
            out.append((method.upper(), path.removeprefix("/api/v1")))
    return sorted(out)


def _call(api: Api, method: str, path: str, ids: dict[str, str]) -> Any:
    url = re.sub(r"\{(\w+)\}", lambda m: ids.get(m.group(1), str(uuid.uuid4())), path)
    body = BODIES.get((method, path))
    if method == "GET":
        return api.get(url)
    if method == "PATCH":
        return api.patch(url, body or {})
    return api.post(url, body)


@pytest.fixture
def victim(make_user: Any, owner_engine: Any) -> dict[str, Any]:
    """User A with one of every resource type."""
    a = make_user()
    t = create_task(a)
    f = upload(a, "victim.csv").json()
    appr = a.post(f"/files/{f['id']}/deletion-request", {"reason": "cleanup"}).json()
    auto = a.post("/automations", BODIES[("POST", "/automations")]).json()
    note = a.get("/notifications").json()["items"][0]["id"]
    ws = a.me["workspace"]["id"]
    with owner_engine.begin() as c:
        art = c.execute(
            text(
                "INSERT INTO artifacts (workspace_id, task_id, name, kind, provenance, storage_key) "
                "VALUES (:w, :t, 'r.md', 'report', 'verified_result', :k) RETURNING id"
            ),
            {"w": ws, "t": t["id"], "k": f"{ws}/{uuid.uuid4().hex}"},
        ).scalar_one()
    return {
        "api": a,
        "ids": {
            "task_id": t["id"],
            "file_id": f["id"],
            "approval_id": appr["id"],
            "automation_id": auto["id"],
            "notification_id": note,
            "artifact_id": str(art),
            "key": "wordpress",
        },
    }


def test_classification_lists_match_real_routes(app: Any) -> None:
    routes = set(_routes(app))
    for entry in PUBLIC | VIEWER_ALLOWED | ADMIN_ONLY | set(BODIES):
        assert entry in routes, f"stale authorization entry {entry}"


def test_anonymous_callers_are_refused_everywhere(app: Any) -> None:
    anon = Api(app)
    for method, path in _routes(app):
        if (method, path) in PUBLIC or path in ("/openapi.json", "/docs"):
            continue
        r = _call(anon, method, path, {})
        assert r.status_code == 401, (method, path, r.status_code)
        assert r.json()["error"]["code"] == "not_authenticated"


def test_cross_tenant_access_returns_not_found_and_changes_nothing(
    app: Any, make_user: Any, victim: dict[str, Any], owner_engine: Any
) -> None:
    attacker = make_user()
    ws = victim["api"].me["workspace"]["id"]
    with owner_engine.connect() as c:
        before = c.execute(
            text(
                "SELECT (SELECT md5(string_agg(t::text, '')) FROM tasks t WHERE workspace_id = :w),"
                " (SELECT md5(string_agg(f::text, '')) FROM files f WHERE workspace_id = :w),"
                " (SELECT md5(string_agg(a::text, '')) FROM approval_requests a WHERE workspace_id = :w),"
                " (SELECT md5(string_agg(x::text, '')) FROM automations x WHERE workspace_id = :w)"
            ),
            {"w": ws},
        ).one()
    checked = 0
    for method, path in _routes(app):
        names = re.findall(r"\{(\w+)\}", path)
        if not names or names == ["key"]:
            continue
        r = _call(attacker, method, path, victim["ids"])
        assert r.status_code == 404, (method, path, r.status_code, r.text[:200])
        checked += 1
    assert checked >= 18
    with owner_engine.connect() as c:
        after = c.execute(
            text(
                "SELECT (SELECT md5(string_agg(t::text, '')) FROM tasks t WHERE workspace_id = :w),"
                " (SELECT md5(string_agg(f::text, '')) FROM files f WHERE workspace_id = :w),"
                " (SELECT md5(string_agg(a::text, '')) FROM approval_requests a WHERE workspace_id = :w),"
                " (SELECT md5(string_agg(x::text, '')) FROM automations x WHERE workspace_id = :w)"
            ),
            {"w": ws},
        ).one()
    assert tuple(before) == tuple(after)
    # The victim's lists don't show anything of the attacker's and vice versa.
    assert all(t["id"] != victim["ids"]["task_id"] for t in attacker.get("/tasks").json()["items"])


def _demote(owner_engine: Any, api: Api, role: str) -> None:
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE workspace_memberships SET role = :r WHERE user_id = :u"),
            {"r": role, "u": api.me["user"]["id"]},
        )


def test_viewer_cannot_use_write_routes(app: Any, victim: dict[str, Any], owner_engine: Any) -> None:
    api = victim["api"]
    _demote(owner_engine, api, "viewer")
    for method, path in _routes(app):
        if method == "GET" or (method, path) in PUBLIC or (method, path) in VIEWER_ALLOWED:
            continue
        r = _call(api, method, path, victim["ids"])
        assert r.status_code == 403, (method, path, r.status_code, r.text[:200])
    # Reads still work.
    assert api.get("/tasks").status_code == 200


def test_member_cannot_use_admin_routes(app: Any, victim: dict[str, Any], owner_engine: Any) -> None:
    api = victim["api"]
    _demote(owner_engine, api, "member")
    for method, path in sorted(ADMIN_ONLY):
        r = _call(api, method, path, victim["ids"])
        assert r.status_code == 403, (method, path, r.status_code, r.text[:200])


def test_authenticated_api_responses_are_never_cacheable(app: Any, victim: dict[str, Any]) -> None:
    api = victim["api"]
    for method, path in _routes(app):
        if method != "GET" or "download" in path:
            continue
        r = _call(api, method, path, victim["ids"])
        cc = r.headers.get("cache-control", "")
        assert "no-store" in cc, (path, cc)
        assert r.headers.get("x-content-type-options") == "nosniff"


def test_model_api_key_never_appears_in_any_response(app: Any, victim: dict[str, Any]) -> None:
    from pydantic import SecretStr

    from app.core.config import get_settings
    from app.engine.providers import OpenAICompatibleProvider, set_provider_override

    key = "sk-proj-SHOULD-NEVER-LEAK-0123456789abcdef"
    s = get_settings().model_copy(
        update={"model_base_url": "https://api.example.com/v1", "model_name": "m", "model_api_key": SecretStr(key)}
    )
    set_provider_override(OpenAICompatibleProvider(s))
    try:
        api = victim["api"]
        for method, path in _routes(app):
            if method != "GET":
                continue
            r = _call(api, method, path, victim["ids"])
            assert key not in r.text, path
            assert "api.example.com/v1" not in r.text or path in ("/agent/status", "/integrations", "/security/status")
    finally:
        set_provider_override(None)
