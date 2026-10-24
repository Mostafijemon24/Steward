"""WordPress write (draft posts only): encrypted credentials, verification, approval binding, policy.

A fake WordPress server checks Basic auth exactly like WordPress does for Application Passwords.
"""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from conftest import Api, create_task, set_workspace
from sqlalchemy import text

from app.core import crypto
from app.engine.providers import MOCK_SCRIPT
from app.engine.tools.wp_tools import secret_purpose

USER = "editor"
APP_PASSWORD = "abcd EFGH 1234 ijkl MNOP 5678"


class FakeWordPress:
    def __init__(self, can_edit: bool = True) -> None:
        self.posts: list[dict[str, Any]] = []
        self.auth_seen: list[str | None] = []
        self.can_edit = can_edit
        outer = self

        class H(BaseHTTPRequestHandler):
            def _authed(self) -> bool:
                h = self.headers.get("Authorization")
                outer.auth_seen.append(h)
                want = "Basic " + base64.b64encode(f"{USER}:{APP_PASSWORD}".encode()).decode()
                return h == want

            def _send(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                if path == "/wp-json/":
                    return self._send(200, {"namespaces": ["oembed/1.0", "wp/v2"]})
                if path == "/wp-json/wp/v2/users/me":
                    if not self._authed():
                        return self._send(401, {"code": "rest_not_logged_in"})
                    return self._send(200, {"id": 2, "capabilities": {"edit_posts": outer.can_edit}})
                return self._send(404, {"code": "rest_no_route"})

            def do_POST(self) -> None:
                if self.path != "/wp-json/wp/v2/posts":
                    return self._send(404, {"code": "rest_no_route"})
                if not self._authed():
                    return self._send(401, {"code": "rest_cannot_create"})
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.posts.append(body)
                return self._send(201, {"id": 100 + len(outer.posts), "status": body.get("status", "publish")})

            def log_message(self, *a: Any) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.srv.shutdown()


@pytest.fixture
def wp() -> Any:
    servers: list[FakeWordPress] = []

    def _make(**kw: Any) -> FakeWordPress:
        s = FakeWordPress(**kw)
        servers.append(s)
        return s

    yield _make
    for s in servers:
        s.close()


def _ws(api: Api) -> str:
    return str(api.me["workspace"]["id"])


def _connect_write(owner_engine: Any, api: Api, site_url: str, password: str = APP_PASSWORD) -> None:
    """Connected WordPress integration with stored (encrypted) write credentials, pointing at a local site."""
    ws = _ws(api)
    sealed = crypto.seal_for_worker(json.dumps({"username": USER, "password": password}).encode(), secret_purpose(ws))
    with owner_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO integrations (workspace_id, key, status, config) VALUES (:w, 'wordpress', 'connected', "
                "CAST(:cfg AS jsonb)) ON CONFLICT (workspace_id, key) DO UPDATE SET status = 'connected', config = EXCLUDED.config"
            ),
            {"w": ws, "cfg": json.dumps({"site_url": site_url, "mode": "read_write_drafts", "username": USER})},
        )
        uid = c.execute(text("SELECT id FROM users WHERE email = :e"), {"e": api.email}).scalar_one()
        c.execute(
            text(
                "INSERT INTO integration_secrets (workspace_id, key, secret_enc, created_by) VALUES (:w, 'wordpress', :s, :u) "
                "ON CONFLICT (workspace_id, key) DO UPDATE SET secret_enc = EXCLUDED.secret_enc, revoked_at = NULL"
            ),
            {"w": ws, "s": sealed, "u": uid},
        )


def _plan(title: str = "Launch notes", content: str = "<p>We launched.</p>") -> None:
    MOCK_SCRIPT.append(
        json.dumps(
            {
                "summary": "Create a draft",
                "steps": [
                    {
                        "goal": "Create the draft post",
                        "tool": "wordpress.create_draft",
                        "args": {"title": title, "content": content},
                        "expected_output": "A draft post",
                    }
                ],
            }
        )
    )


def _approve_everything(api: Api, task_id: str) -> None:
    t = api.get(f"/tasks/{task_id}").json()
    ids = [s["id"] for s in t["plan"]["steps"] if s["state"] != "blocked"]
    assert api.post(f"/tasks/{task_id}/plan/approve", {"selected_step_ids": ids}).status_code == 200


def _decide_pending(api: Api, task_id: str) -> list[dict[str, Any]]:
    pending = [
        a for a in api.get("/approvals", params={"status": "pending"}).json()["items"] if a["task_id"] == task_id
    ]
    for a in pending:
        r = api.post(f"/approvals/{a['id']}/decision", {"decision": "approve", "acknowledged": True})
        assert r.status_code == 200, r.text
    return pending


# ---------------------------------------------------------------- API: storing credentials
def test_connect_stores_credentials_encrypted_and_never_returns_them(
    make_user: Any, owner_engine: Any, role_engines: Any
) -> None:
    api = make_user()
    set_workspace(owner_engine, api, allowed_domains=["blog.example.com"])
    r = api.post(
        "/integrations/wordpress/connect",
        {"site_url": "https://blog.example.com", "username": USER, "application_password": APP_PASSWORD},
    )
    assert r.status_code == 202, r.text
    assert APP_PASSWORD not in r.text and "abcd" not in r.text
    wp_out = next(i for i in api.get("/integrations").json() if i["key"] == "wordpress")
    assert wp_out["write_access"] is True and any("DRAFT" in s for s in wp_out["scopes"])
    assert APP_PASSWORD not in json.dumps(api.get("/integrations").json())
    with owner_engine.connect() as c:
        stored = c.execute(
            text("SELECT secret_enc FROM integration_secrets WHERE workspace_id = :w"), {"w": _ws(api)}
        ).scalar_one()
        audit = c.execute(
            text("SELECT message, metadata::text FROM audit_events WHERE workspace_id = :w"), {"w": _ws(api)}
        ).all()
    assert stored.startswith("x1:") and APP_PASSWORD not in stored
    assert json.loads(crypto.unseal_on_worker(stored, secret_purpose(_ws(api))))["password"] == APP_PASSWORD
    assert all(APP_PASSWORD not in m and APP_PASSWORD not in (meta or "") for m, meta in audit)
    # The API's own database role cannot read the credential column at all.
    with role_engines["app"].connect() as c, pytest.raises(Exception, match="permission denied"):
        c.execute(text("SELECT secret_enc FROM integration_secrets"))
    # Disconnecting wipes the stored credential.
    assert api.post("/integrations/wordpress/disconnect").status_code == 200
    with owner_engine.connect() as c:
        row = c.execute(
            text("SELECT secret_enc, revoked_at FROM integration_secrets WHERE workspace_id = :w"), {"w": _ws(api)}
        ).one()
    assert row[0] is None and row[1] is not None


@pytest.mark.parametrize(
    "body",
    [
        {"site_url": "https://blog.example.com", "username": USER},
        {"site_url": "https://blog.example.com", "application_password": APP_PASSWORD},
        {"site_url": "https://blog.example.com", "username": USER, "application_password": "short"},
        {"site_url": "http://blog.example.com", "username": USER, "application_password": APP_PASSWORD},
    ],
)
def test_connect_validation(make_user: Any, owner_engine: Any, body: dict[str, Any]) -> None:
    api = make_user()
    set_workspace(owner_engine, api, allowed_domains=["blog.example.com"])
    r = api.post("/integrations/wordpress/connect", body)
    assert r.status_code == 422
    assert APP_PASSWORD not in r.text


def test_worker_verifies_credentials_before_write_counts_as_connected(
    make_user: Any, engine: Any, owner_engine: Any, wp: Any
) -> None:
    site = wp()
    api = make_user()
    set_workspace(owner_engine, api, allowed_domains=["blog.example.com", "127.0.0.1"])
    for password, expect in [("wrong password value 1", "error"), (APP_PASSWORD, "connected")]:
        r = api.post(
            "/integrations/wordpress/connect",
            {"site_url": "https://blog.example.com", "username": USER, "application_password": password},
        )
        assert r.status_code == 202
        with owner_engine.begin() as c:  # point the stored site at the local fake (https is required by the API)
            c.execute(
                text(
                    "UPDATE integrations SET config = jsonb_set(config, '{site_url}', to_jsonb(CAST(:u AS text))) "
                    "WHERE workspace_id = :w AND key = 'wordpress'"
                ),
                {"u": site.url, "w": _ws(api)},
            )
        engine.drain()
        wp_out = next(i for i in api.get("/integrations").json() if i["key"] == "wordpress")
        assert wp_out["status"] == expect, wp_out
        if expect == "error":
            assert "refused" in wp_out["error_message"]
        with owner_engine.begin() as c:  # allow the next check in the same minute
            c.execute(text("UPDATE jobs SET dedupe_key = NULL WHERE kind = 'integration.verify'"))


def test_user_without_edit_rights_is_rejected(make_user: Any, engine: Any, owner_engine: Any, wp: Any) -> None:
    site = wp(can_edit=False)
    api = make_user()
    set_workspace(owner_engine, api, allowed_domains=["blog.example.com", "127.0.0.1"])
    api.post(
        "/integrations/wordpress/connect",
        {"site_url": "https://blog.example.com", "username": USER, "application_password": APP_PASSWORD},
    )
    with owner_engine.begin() as c:
        c.execute(
            text(
                "UPDATE integrations SET config = jsonb_set(config, '{site_url}', to_jsonb(CAST(:u AS text))) "
                "WHERE workspace_id = :w AND key = 'wordpress'"
            ),
            {"u": site.url, "w": _ws(api)},
        )
    engine.drain()
    wp_out = next(i for i in api.get("/integrations").json() if i["key"] == "wordpress")
    assert wp_out["status"] == "error" and "not allowed to create posts" in wp_out["error_message"]


# ---------------------------------------------------------------- engine: drafts
def test_draft_needs_approval_and_is_always_a_draft(make_user: Any, engine: Any, owner_engine: Any, wp: Any) -> None:
    site = wp()
    api = make_user()
    set_workspace(
        owner_engine, api, allowed_domains=["127.0.0.1"], tool_permissions={"wp_read": True, "wp_write": True}
    )
    _connect_write(owner_engine, api, site.url)
    _plan()
    t = create_task(api, "Write a draft post about our launch.")
    engine.drain()
    t = api.get(f"/tasks/{t['id']}").json()
    (step,) = t["plan"]["steps"]
    assert step["requires_approval"] is True and step["policy"]["outcome"] == "require_approval"
    assert site.posts == []  # nothing sent before approval
    _approve_everything(api, t["id"])
    engine.drain()
    approvals = _decide_pending(api, t["id"])
    assert approvals and "Launch notes" in json.dumps(approvals[0])  # the approval shows the exact content
    engine.drain()
    assert site.posts == [{"title": "Launch notes", "content": "<p>We launched.</p>", "excerpt": "", "status": "draft"}]
    t = api.get(f"/tasks/{t['id']}").json()
    assert t["plan"]["steps"][0]["state"] == "done"
    # The credential never appears anywhere the app stores task or audit data.
    with owner_engine.connect() as c:
        for table in ("task_events", "task_steps", "audit_events", "jobs", "tasks", "approval_requests"):
            dump = c.execute(text(f"SELECT CAST(json_agg(t) AS text) FROM {table} t")).scalar_one() or ""  # noqa: S608
            assert APP_PASSWORD not in dump and base64.b64encode(f"{USER}:{APP_PASSWORD}".encode()).decode() not in dump


def test_changed_content_after_approval_is_not_sent(make_user: Any, engine: Any, owner_engine: Any, wp: Any) -> None:
    site = wp()
    api = make_user()
    set_workspace(
        owner_engine, api, allowed_domains=["127.0.0.1"], tool_permissions={"wp_read": True, "wp_write": True}
    )
    _connect_write(owner_engine, api, site.url)
    _plan()
    t = create_task(api, "Write a draft post about our launch.")
    engine.drain()
    _approve_everything(api, t["id"])
    engine.drain()
    _decide_pending(api, t["id"])
    with owner_engine.begin() as c:  # tamper with the approved arguments before the worker runs
        c.execute(
            text(
                "UPDATE task_steps SET args = jsonb_set(args, '{content}', '\"<p>Something else entirely.</p>\"') "
                "WHERE task_id = :t"
            ),
            {"t": t["id"]},
        )
    engine.drain()
    assert site.posts == []
    step = api.get(f"/tasks/{t['id']}").json()["plan"]["steps"][0]
    assert step["state"] in ("failed", "not_started", "blocked")
    events = [e["message"] for e in api.get(f"/tasks/{t['id']}/events").json()["items"]]
    assert any("arguments changed" in m or "was not run" in m for m in events), events


def test_revoked_credentials_fail_without_retrying(make_user: Any, engine: Any, owner_engine: Any, wp: Any) -> None:
    site = wp()
    api = make_user()
    set_workspace(
        owner_engine, api, allowed_domains=["127.0.0.1"], tool_permissions={"wp_read": True, "wp_write": True}
    )
    _connect_write(owner_engine, api, site.url, password="revoked password value")
    _plan()
    t = create_task(api, "Write a draft post about our launch.")
    engine.drain()
    _approve_everything(api, t["id"])
    engine.drain()
    _decide_pending(api, t["id"])
    engine.drain()
    assert site.posts == [] and len([a for a in site.auth_seen if a]) == 1  # one attempt, not retried
    t = api.get(f"/tasks/{t['id']}").json()
    assert t["plan"]["steps"][0]["state"] == "failed"


@pytest.mark.parametrize(
    ("perms", "domains", "content", "rule"),
    [
        ({"wp_read": True, "wp_write": False}, ["127.0.0.1"], "<p>ok</p>", "turned off"),
        (
            {"wp_read": True, "wp_write": True},
            ["other.example.com"],
            "<p>ok</p>",
            "not on this workspace's allowed domains",
        ),
        ({"wp_read": True, "wp_write": True}, ["127.0.0.1"], '<p onclick="x()">hi</p>', None),
        ({"wp_read": True, "wp_write": True}, ["127.0.0.1"], "<script>alert(1)</script>", None),
    ],
)
def test_policy_blocks_unsafe_drafts(
    make_user: Any,
    engine: Any,
    owner_engine: Any,
    wp: Any,
    perms: dict[str, bool],
    domains: list[str],
    content: str,
    rule: str | None,
) -> None:
    site = wp()
    api = make_user()
    set_workspace(owner_engine, api, allowed_domains=domains, tool_permissions=perms)
    _connect_write(owner_engine, api, site.url)
    _plan(content=content)
    t = create_task(api, "Write a draft post about our launch.")
    engine.drain()
    t = api.get(f"/tasks/{t['id']}").json()
    if rule:
        (step,) = t["plan"]["steps"]
        assert step["policy"]["outcome"] == "deny" and any(rule in r for r in step["policy"]["reasons"])
    else:  # active content: the arguments are invalid, so no runnable step exists
        steps = (t.get("plan") or {}).get("steps") or []
        assert all(s["state"] == "blocked" for s in steps) or t["status"] == "failed"
    assert site.posts == []


def test_old_generic_write_tool_stays_unavailable(make_user: Any) -> None:
    api = make_user()
    tools = {t["id"]: t for t in api.get("/agent/tools").json()}
    assert tools["wordpress.write"]["available"] is False
    assert "never edits, publishes or deletes" in tools["wordpress.write"]["unavailable_reason"]
    assert tools["wordpress.create_draft"]["available"] is False  # switched off by default (wp_write)


# ---------------------------------------------------------------- review finding P5-R1: allowlist, not denylist
BYPASS_PAYLOADS = [
    '<a href="jav&#x61;script:alert(1)">x</a>',
    '<a href="javascript&colon;alert(1)">x</a>',
    '<a href="data:text/html;base64,PHNjcmlwdD4=">x</a>',
    '<a xlink:href="jav&#9;ascript:alert(1)">x</a>',
    '<a href="&#106;avascript:alert(1)">x</a>',
    '<a href="java\x00script:alert(1)">x</a>',
    '<a href=" javascript:alert(1)">x</a>',
    '<A HREF="JaVaScRiPt:alert(1)">x</A>',
    "<img src=x onerror=alert(1)>",
    '<img src="http://insecure.example/y.png">',
    "<script>alert(1)</script>",
    '<svg><a href="https://x.example">x</a></svg>',
    '<p style="background:url(javascript:x)">x</p>',
    '<iframe src="https://x.example"></iframe>',
    '<a href="//evil.example/x">x</a>',
    "<!-- hidden -->",
    "text <scr",
    "< script>alert(1)</script>",
]
SAFE_DRAFTS = [
    "<p>We launched <strong>today</strong>.</p>",
    '<a href="https://example.com/x?a=1&amp;b=2">link</a>',
    "<ul><li>one</li><li>two</li></ul>",
    '<a href="/about">About</a> and <a href="mailto:hi@example.com">mail</a>',
    '<img src="https://cdn.example.com/a.png" alt="chart">',
    "Fish &amp; chips &lt; 10 euro",
    "Line one<br>Line two",
]


@pytest.mark.parametrize("payload", BYPASS_PAYLOADS)
def test_draft_content_allowlist_refuses_active_content(payload: str) -> None:
    from pydantic import ValidationError

    from app.engine.tools.wp_tools import WordPressDraftArgs

    with pytest.raises(ValidationError):
        WordPressDraftArgs(title="t", content=payload)


@pytest.mark.parametrize("content", SAFE_DRAFTS)
def test_draft_content_allowlist_keeps_simple_formatting(content: str) -> None:
    from app.engine.tools.wp_tools import WordPressDraftArgs

    assert WordPressDraftArgs(title="Q&amp;A night", content=content).content == content


def test_titles_and_excerpts_are_plain_text() -> None:
    from pydantic import ValidationError

    from app.engine.tools.wp_tools import WordPressDraftArgs

    for field in ("title", "excerpt"):
        with pytest.raises(ValidationError):
            WordPressDraftArgs(**{"title": "t", "content": "c", field: "<b>bold</b>"})


def test_destination_change_after_approval_blocks_the_send(
    make_user: Any, engine: Any, owner_engine: Any, wp: Any
) -> None:
    """P5-R5: reconnecting WordPress to another site after approval must not redirect approved content."""
    site_a, site_b = wp(), wp()
    api = make_user()
    set_workspace(
        owner_engine,
        api,
        allowed_domains=["127.0.0.1", "localhost"],
        tool_permissions={"wp_read": True, "wp_write": True},
    )
    _connect_write(owner_engine, api, site_a.url)
    _plan()
    t = create_task(api, "Write a draft post about our launch.")
    engine.drain()
    _approve_everything(api, t["id"])
    engine.drain()
    _decide_pending(api, t["id"])
    _connect_write(owner_engine, api, site_b.url.replace("127.0.0.1", "localhost"))  # another host
    engine.drain()
    assert site_a.posts == [] and site_b.posts == []
    events = [e["message"] for e in api.get(f"/tasks/{t['id']}/events").json()["items"]]
    assert any("destination changed" in m for m in events), events


# ---------------------------------------------------------------- review finding P5-R6: API cannot decrypt
def test_integration_credentials_need_the_workers_private_key(monkeypatch: Any) -> None:
    from pydantic import SecretStr, ValidationError

    from app.core.config import DEV_INTEGRATION_PUBLIC_KEY, Settings, get_settings

    keys = crypto.keygen()
    s = get_settings()
    monkeypatch.setattr(s, "integration_secrets_public_key", keys["INTEGRATION_SECRETS_PUBLIC_KEY"])
    sealed = crypto.seal_for_worker(b'{"password": "x"}', "integration:wordpress:ws-1")
    assert sealed.startswith("x1:")
    # The API process has no private key (here: the public development one) -> cannot decrypt.
    with pytest.raises(crypto.DecryptionError):
        crypto.unseal_on_worker(sealed, "integration:wordpress:ws-1")
    # The MFA key is unrelated to integration credentials.
    with pytest.raises(crypto.DecryptionError):
        crypto.unseal(sealed, "integration:wordpress:ws-1")
    monkeypatch.setattr(s, "integration_secrets_private_key", SecretStr(keys["INTEGRATION_SECRETS_PRIVATE_KEY"]))
    assert crypto.unseal_on_worker(sealed, "integration:wordpress:ws-1") == b'{"password": "x"}'
    with pytest.raises(crypto.DecryptionError):  # bound to the workspace
        crypto.unseal_on_worker(sealed, "integration:wordpress:ws-2")
    # Production refuses the development public key for the API, and a dev "previous" MFA key.
    base: dict[str, Any] = {
        "env": "production",
        "app_origin": "https://app.example.com",
        "mail_backend": "smtp",
        "smtp_host": "smtp.example.com",
        "trust_proxy_headers": True,
        "database_url": "postgresql+psycopg://a:Str0ng-Random-Value@db/x",
        "approval_signing_key": "k" * 40,
        "secrets_encryption_key": keys["SECRETS_ENCRYPTION_KEY"],
        "integration_secrets_public_key": keys["INTEGRATION_SECRETS_PUBLIC_KEY"],
    }
    Settings(**base)
    with pytest.raises(ValidationError, match="INTEGRATION_SECRETS_PUBLIC_KEY"):
        Settings(**{**base, "integration_secrets_public_key": DEV_INTEGRATION_PUBLIC_KEY})
    from app.core.config import DEV_SECRETS_KEY

    with pytest.raises(ValidationError, match="PREVIOUS_KEY"):
        Settings(**{**base, "secrets_encryption_previous_key": DEV_SECRETS_KEY})
    # A production worker with the development private key refuses to decrypt.
    monkeypatch.setattr(s, "env", "production")
    from app.core.config import DEV_INTEGRATION_PRIVATE_KEY

    monkeypatch.setattr(s, "integration_secrets_private_key", SecretStr(DEV_INTEGRATION_PRIVATE_KEY))
    with pytest.raises(crypto.DecryptionError, match="not configured"):
        crypto.unseal_on_worker(sealed, "integration:wordpress:ws-1")
