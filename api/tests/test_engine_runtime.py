"""Queue semantics, workers, provider gateway, egress, redaction, sandbox and scheduler."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.enums import DataClass, JobQueue
from app.engine import queue as Q
from app.engine.egress import EgressBlocked, EgressClient, EgressPolicy, check_url, resolve_checked
from app.engine.providers import MOCK_SCRIPT, ModelError, ModelGateway, ModelMessage, OpenAICompatibleProvider
from app.engine.redaction import redact
from tests.conftest import create_task


def _ws(api: Any) -> uuid.UUID:
    return uuid.UUID(api.me["workspace"]["id"])


# ---------------------------------------------------------------- queue
def test_enqueue_is_idempotent_by_dedupe_key(make_user: Any, engine: Any) -> None:
    api = make_user()
    with engine.worker_factory() as db:
        a = Q.enqueue(
            db, workspace_id=_ws(api), kind="x", queue=JobQueue.tools, dedupe_key="k1", settings=engine.settings
        )
        b = Q.enqueue(
            db, workspace_id=_ws(api), kind="x", queue=JobQueue.tools, dedupe_key="k1", settings=engine.settings
        )
        db.commit()
    assert a == b


def test_concurrent_claims_never_hand_out_a_job_twice(make_user: Any, engine: Any) -> None:
    api = make_user()
    s = engine.settings.model_copy(update={"queue_max_running_per_workspace": 100})
    with engine.worker_factory() as db:
        ids = {Q.enqueue(db, workspace_id=_ws(api), kind="noop", queue=JobQueue.tools, settings=s) for _ in range(12)}
        db.commit()
    claimed: list[uuid.UUID] = []
    lock = threading.Lock()

    def grab() -> None:
        while True:
            with engine.worker_factory() as db:
                j = Q.claim(db, JobQueue.tools, f"w-{threading.get_ident()}", s)
            if j is None:
                return
            with lock:
                claimed.append(j.id)

    threads = [threading.Thread(target=grab) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == sorted(ids) and len(claimed) == len(set(claimed))


def test_retry_with_backoff_then_dead_letter(make_user: Any, engine: Any) -> None:
    api = make_user()
    with engine.worker_factory() as db:
        jid = Q.enqueue(
            db, workspace_id=_ws(api), kind="nope", queue=JobQueue.tools, max_attempts=3, settings=engine.settings
        )
        db.commit()
    statuses = []
    for _ in range(3):
        with engine.worker_factory() as db:
            j = Q.claim(db, JobQueue.tools, "w1", engine.settings)
            assert j is not None and j.id == jid
            statuses.append(Q.fail(db, j, "boom", retryable=True, settings=engine.settings))
            db.commit()
    assert statuses == ["pending", "pending", "dead"]
    with engine.worker_factory() as db:
        assert Q.claim(db, JobQueue.tools, "w1", engine.settings) is None


def test_backoff_grows_and_is_bounded() -> None:
    s = Settings(job_backoff_base_seconds=2, job_backoff_max_seconds=30)
    for attempt, ceiling in [(1, 2), (2, 4), (3, 8), (10, 30)]:
        v = Q.backoff_seconds(attempt, s)
        assert ceiling / 2 <= v <= ceiling


def test_unknown_job_kind_fails_without_retry(make_user: Any, engine: Any, owner_engine: Any) -> None:
    api = make_user()
    with engine.worker_factory() as db:
        jid = Q.enqueue(db, workspace_id=_ws(api), kind="evil.kind", queue=JobQueue.tools, settings=engine.settings)
        db.commit()
    engine.drain()
    with owner_engine.connect() as c:
        st = c.execute(text("SELECT status, attempts FROM jobs WHERE id = :j"), {"j": jid}).one()
    assert (st.status, st.attempts) == ("failed", 1)


def test_stale_running_job_is_recovered(make_user: Any, engine: Any, owner_engine: Any) -> None:
    api = make_user()
    with engine.worker_factory() as db:
        jid = Q.enqueue(
            db, workspace_id=_ws(api), kind="x", queue=JobQueue.tools, max_attempts=2, settings=engine.settings
        )
        db.commit()
        Q.claim(db, JobQueue.tools, "dead-worker", engine.settings)
    with owner_engine.begin() as c:
        c.execute(text("UPDATE jobs SET heartbeat_at = now() - interval '1 hour' WHERE id = :j"), {"j": jid})
    with engine.worker_factory() as db:
        assert Q.recover_stale(db, engine.settings) == 1
    with owner_engine.connect() as c:
        st = c.execute(text("SELECT status, last_error FROM jobs WHERE id = :j"), {"j": jid}).one()
    assert st.status == "pending" and "stopped responding" in st.last_error


def test_backpressure_and_fair_share(make_user: Any, engine: Any) -> None:
    a, b = make_user(), make_user()
    s = engine.settings.model_copy(update={"queue_max_pending_per_workspace": 2, "queue_max_running_per_workspace": 1})
    with engine.worker_factory() as db:
        Q.enqueue(db, workspace_id=_ws(a), kind="x", queue=JobQueue.tools, settings=s)
        Q.enqueue(db, workspace_id=_ws(a), kind="x", queue=JobQueue.tools, settings=s)
        with pytest.raises(Q.QueueFull):
            Q.enqueue(db, workspace_id=_ws(a), kind="x", queue=JobQueue.tools, settings=s)
        Q.enqueue(db, workspace_id=_ws(b), kind="x", queue=JobQueue.tools, settings=s)
        db.commit()
        first = Q.claim(db, JobQueue.tools, "w", s)
        second = Q.claim(db, JobQueue.tools, "w", s)
        third = Q.claim(db, JobQueue.tools, "w", s)
    assert first is not None and second is not None
    assert {first.workspace_id, second.workspace_id} == {_ws(a), _ws(b)}  # A's second job waits
    assert third is None


def test_transient_model_failure_retries_then_fails_task_visibly(make_user: Any, engine: Any) -> None:
    api = make_user()
    engine.settings.model_max_retries = 0
    MOCK_SCRIPT.extend([ModelError("timeout", "slow", retryable=True)] * 3)
    t = create_task(api, "Write a report about nothing in particular please.")
    engine.drain()
    t = api.get(f"/tasks/{t['id']}").json()
    assert t["status"] == "failed"
    events = [e["message"] for e in api.get(f"/tasks/{t['id']}/events").json()["items"]]
    assert sum("retrying shortly" in m for m in events) == 2


def test_cancel_stops_queued_work(make_user: Any, engine: Any, owner_engine: Any) -> None:
    api = make_user()
    t = create_task(api, "Write a report about nothing in particular please.")
    engine.drain()
    t = api.get(f"/tasks/{t['id']}").json()
    api.post(f"/tasks/{t['id']}/plan/approve", {"selected_step_ids": [s["id"] for s in t["plan"]["steps"]]})
    r = api.post(f"/tasks/{t['id']}/cancel", {"reason": "changed my mind"})
    assert r.json()["status"] == "cancelled"
    engine.drain()
    t = api.get(f"/tasks/{t['id']}").json()
    assert t["status"] == "cancelled" and all(s["state"] in ("cancelled", "skipped") for s in t["plan"]["steps"])
    assert t["artifacts"] == []
    with owner_engine.connect() as c:
        st = c.execute(
            text("SELECT status FROM jobs WHERE task_id = :t AND kind = 'task.execute'"), {"t": t["id"]}
        ).scalar_one()
    assert st == "cancelled"


def test_pause_and_resume(make_user: Any, engine: Any, site: Any, owner_engine: Any) -> None:
    from tests.conftest import set_workspace

    site.add("/", "<title>x</title>")
    api = make_user()
    set_workspace(owner_engine, api, allowed_domains=["127.0.0.1"], privacy_mode="approval")
    t = create_task(api, f"Inspect {site.url}/ and write a report.")
    engine.drain()
    t = api.get(f"/tasks/{t['id']}").json()
    api.post(f"/tasks/{t['id']}/plan/approve", {"selected_step_ids": [s["id"] for s in t["plan"]["steps"]]})
    engine.orchestrator.run_once()  # starts and queues step 1
    assert api.post(f"/tasks/{t['id']}/pause").json()["status"] == "paused"
    engine.drain()
    t = api.get(f"/tasks/{t['id']}").json()
    assert t["status"] == "paused" and [s["state"] for s in t["plan"]["steps"]] == ["done", "not_started"]
    api.post(f"/tasks/{t['id']}/resume")
    engine.drain()
    assert api.get(f"/tasks/{t['id']}").json()["status"] == "completed"


def test_worker_heartbeat_rows(make_user: Any, engine: Any, owner_engine: Any) -> None:
    from app.workers.runtime import Worker

    w = Worker("tools", engine.worker_factory, engine.settings)
    w._register()
    w._beat(stopped=True)
    with owner_engine.connect() as c:
        row = c.execute(text("SELECT kind, stopped_at FROM workers WHERE id = :i"), {"i": w.id}).one()
    assert row.kind == "tools" and row.stopped_at is not None


# ---------------------------------------------------------------- provider gateway
def _gw(db: Session, settings: Settings) -> ModelGateway:
    return ModelGateway(db, settings, sleep=lambda _s: None)


def test_gateway_retries_transient_errors_and_records_usage(make_user: Any, engine: Any, owner_engine: Any) -> None:
    api = make_user()
    engine.settings.model_max_retries = 2
    MOCK_SCRIPT.extend([ModelError("timeout", "slow", retryable=True), "fine"])
    with engine.worker_factory() as db:
        res = _gw(db, engine.settings).call(
            workspace_id=_ws(api),
            task_id=None,
            purpose="summarize",
            messages=[ModelMessage("user", "hi")],
            classification=DataClass.public,
            external_transfer_authorised=False,
        )
        db.commit()
    assert res.response.text == "fine" and res.attempts == 2
    with owner_engine.connect() as c:
        rows = (
            c.execute(
                text("SELECT outcome FROM model_calls WHERE workspace_id = :w ORDER BY created_at"), {"w": _ws(api)}
            )
            .scalars()
            .all()
        )
    assert rows == ["timeout", "ok"]


@pytest.mark.parametrize(
    ("update", "code"),
    [({"monthly_model_call_quota": 0}, "quota_exceeded"), ({"model_max_input_chars": 5}, "input_too_large")],
)
def test_gateway_limits(make_user: Any, engine: Any, update: dict[str, int], code: str) -> None:
    api = make_user()
    s = engine.settings.model_copy(update=update)
    with engine.worker_factory() as db, pytest.raises(ModelError) as e:
        _gw(db, s).call(
            workspace_id=_ws(api),
            task_id=None,
            purpose="plan",
            messages=[ModelMessage("user", "hello world")],
            classification=DataClass.public,
            external_transfer_authorised=False,
        )
    assert e.value.code == code


def test_mock_provider_and_insecure_egress_are_refused_in_production() -> None:
    base = dict(
        env="production",
        database_url="postgresql+psycopg://steward_app:Str0ng-random-pw@db/steward",
        approval_signing_key="x" * 40,
        mail_backend="smtp",
        smtp_host="smtp.example.com",
        app_origin="https://app.example.com",
        trust_proxy_headers=True,
        secrets_encryption_key="cmFuZG9tLWtleS1mb3ItdGVzdHMtMzItYnl0ZXMhISE=",
        integration_secrets_public_key="cHVibGljLWtleS1mb3ItdGVzdHMtMzItYnl0ZXMhISE=",
    )
    with pytest.raises(ValueError, match="mock"):
        Settings(_env_file=None, **base, model_provider="mock")
    with pytest.raises(ValueError, match="https"):
        Settings(
            _env_file=None,
            **base,
            model_provider="openai_compatible",
            model_base_url="http://api.example.com/v1",
            model_name="m",
        )
    with pytest.raises(ValueError, match="EGRESS"):
        Settings(_env_file=None, **base, egress_allow_private_networks=True)
    Settings(
        _env_file=None,
        **base,
        model_provider="openai_compatible",
        model_base_url="https://api.example.com/v1",
        model_name="m",
    )


class _FakeOpenAI:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.queue: list[tuple[int, dict[str, Any] | None]] = []

    def start(self) -> str:
        fake = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                status, payload = fake.queue.pop(0) if fake.queue else (200, None)
                data = json.dumps(
                    payload
                    or {
                        "model": "m1",
                        "choices": [{"message": {"content": "hello"}}],
                        "usage": {"prompt_tokens": 7, "completion_tokens": 2},
                    }
                ).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a: Any) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}/v1"


def test_openai_compatible_adapter_against_a_local_server(make_user: Any, engine: Any, owner_engine: Any) -> None:
    fake = _FakeOpenAI()
    base = fake.start()
    s = engine.settings.model_copy(
        update={
            "model_provider": "openai_compatible",
            "model_base_url": base,
            "model_name": "m1",
            "model_location": "local",
            "model_api_key": SecretStr("sk-local-secret-123456789012"),
            "model_max_retries": 1,
        }
    )
    p = OpenAICompatibleProvider(s)
    assert p.info.configured and p.info.location == "local"
    api = make_user()
    fake.queue.append((429, {"error": "slow down"}))
    with engine.worker_factory() as db:
        res = ModelGateway(db, s, provider=p, sleep=lambda _x: None).call(
            workspace_id=_ws(api),
            task_id=None,
            purpose="summarize",
            messages=[ModelMessage("user", "email me at a@b.co")],
            classification=DataClass.confidential,
            external_transfer_authorised=False,
        )
        db.commit()
    assert res.response.text == "hello" and res.response.input_tokens == 7 and res.attempts == 2
    assert fake.requests[0]["auth"] == "Bearer sk-local-secret-123456789012"
    assert fake.requests[0]["path"] == "/v1/chat/completions" and fake.requests[0]["body"]["temperature"] == 0
    with owner_engine.connect() as c:
        dump = json.dumps(
            [
                list(r)
                for r in c.execute(text("SELECT * FROM model_calls WHERE workspace_id = :w"), {"w": _ws(api)}).all()
            ],
            default=str,
        )
    assert "sk-local-secret" not in dump and "a@b.co" not in dump  # key and prompt never stored
    fake.srv.shutdown()


def test_external_provider_must_use_allowlisted_host_only(engine: Any) -> None:
    s = engine.settings.model_copy(
        update={"model_base_url": "https://api.example.com/v1", "model_name": "m", "model_location": "external"}
    )
    p = OpenAICompatibleProvider(s)
    assert p.policy.allowed_hosts == frozenset({"api.example.com"}) and p.policy.allow_private is False
    assert p.policy.allowed_ports == frozenset({443})


# ---------------------------------------------------------------- egress
STRICT = EgressPolicy(allowed_hosts=frozenset({"example.com"}))


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("ftp://example.com/x", "scheme_not_allowed"),
        ("http://example.com/", "scheme_not_allowed"),
        ("https://user:pw@example.com/", "credentials_in_url"),
        ("https://example.com:8443/", "port_not_allowed"),
        ("https://evil.com/", "domain_not_allowed"),
        ("https://example.com.evil.com/", "domain_not_allowed"),
    ],
)
def test_egress_rejects_unsafe_urls(url: str, code: str) -> None:
    with pytest.raises(EgressBlocked) as e:
        check_url(url, STRICT)
    assert e.value.code == code


def test_subdomains_of_allowed_domains_are_allowed() -> None:
    assert check_url("https://blog.example.com/a?b=1", STRICT)[1] == "blog.example.com"


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.0.0.8", "169.254.169.254", "192.168.1.1", "::1", "0.0.0.0", "::ffff:127.0.0.1", "fec0::1"],  # noqa: S104
)
def test_private_and_metadata_addresses_are_blocked(ip: str) -> None:
    pol = EgressPolicy(allowed_hosts=frozenset({ip}))
    with pytest.raises(EgressBlocked) as e:
        resolve_checked(ip, 443, pol)
    assert e.value.code == "private_address"


def test_cross_host_redirect_and_size_cap(site: Any, engine: Any) -> None:
    port = site.url.rsplit(":", 1)[1]
    site.add("/go", "", status=302, headers={"Location": f"http://localhost:{port}/"})
    site.add("/same", "", status=302, headers={"Location": "/big"})
    site.add("/big", "x" * 5000)
    s = engine.settings.model_copy(update={"egress_max_bytes": 1000})
    pol = EgressPolicy(
        allowed_hosts=frozenset({"127.0.0.1", "localhost"}),
        allow_private=True,
        allow_http=True,
        allowed_ports=frozenset({int(port)}),
    )
    c = EgressClient(pol, s)
    with pytest.raises(EgressBlocked) as e:
        c.get(f"{site.url}/go")
    assert e.value.code == "cross_host_redirect"
    r = c.get(f"{site.url}/same")
    assert r.status == 200 and len(r.body) == 1000 and r.truncated is True


# ---------------------------------------------------------------- redaction
@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-abcdefghijklmnopqrstuv",
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_abcdefghijklmnopqrstuvwxyz0123",
        "xoxb-1234567890-abcdefghij",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        "postgresql://app:hunter22@db.internal/steward",
        "4111 1111 1111 1111",
    ],
)
def test_redaction_removes_common_secrets(secret: str) -> None:
    r = redact(f"before {secret} after")
    assert secret not in r.text and r.count >= 1 and r.text.startswith("before")


def test_redaction_keeps_ordinary_text_and_json_valid() -> None:
    assert redact("Order 1234 shipped on 2026-10-24.").count == 0
    out = redact(json.dumps({"p": "token: abc123secret, more"})).text
    assert json.loads(out)["p"].startswith("token: [REDACTED]")


@pytest.mark.parametrize(
    "text_in",
    [
        "DB_PASSWORD=hunter2",
        '{"password": "hunter2xyz"}',
        json.dumps(json.dumps({"api_key": "abcdef123456"})),
        "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG",
        "Authorization: Basic dXNlcjpwYXNzd29yZA==",
        "client_secret: 'ab12cd34ef'",
    ],
)
def test_redaction_formats_from_review(text_in: str) -> None:
    """P3-SR-3 regression."""
    out = redact(text_in).text
    for secret in ("hunter2", "abcdef123456", "wJalrXUtnFEMI", "dXNlcjpwYXNzd29yZA", "ab12cd34ef"):
        assert secret not in out, out
    if text_in.startswith(("{", '"')):
        json.loads(out)  # still valid JSON


# ---------------------------------------------------------------- sandbox
def _sandbox_or_skip() -> None:
    from app.engine.sandbox import isolation_status

    ok, why = isolation_status()
    if not ok:
        pytest.skip(why)


def test_sandbox_isolation() -> None:
    from app.engine.sandbox import run_python

    _sandbox_or_skip()
    code = (
        "import os, socket\n"
        "print(sorted(os.environ))\n"
        "try:\n socket.create_connection(('1.1.1.1', 80), 1); print('NET')\nexcept OSError: print('nonet')\n"
        "print(len([p for p in os.listdir('/proc') if p.isdigit()]) <= 3)\n"
        "try:\n open('/etc/passwd-x', 'w'); print('WROTE')\nexcept OSError: print('readonly')\n"
    )
    r = run_python(code)
    assert r.exit_code == 0, r.stderr
    lines = r.stdout.splitlines()
    assert "DATABASE_URL" not in lines[0] and "MODEL_API_KEY" not in lines[0]
    assert lines[1:] == ["nonet", "True", "readonly"]


def test_sandbox_hides_storage_and_app_dirs() -> None:
    from app.core.config import get_settings
    from app.engine.sandbox import run_python

    _sandbox_or_skip()
    storage = str(get_settings().storage_dir.resolve())
    r = run_python(f"import os\np = {storage!r}\nprint(os.listdir(p) if os.path.exists(p) else 'absent')")
    assert r.stdout.strip() in ("[]", "absent"), r.stderr


def test_sandbox_limits() -> None:
    from app.engine.sandbox import run_python

    _sandbox_or_skip()
    s = Settings(sandbox_timeout_seconds=2)
    assert run_python("while True: pass", s).timed_out is True
    assert run_python("x = bytearray(2 * 1024 ** 3)").exit_code != 0
    big = run_python("print('a' * 200000)")
    assert big.truncated is True


# ---------------------------------------------------------------- scheduler
def test_next_run_calculation() -> None:
    from types import SimpleNamespace

    from app.engine.scheduler import next_run

    after = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)  # a Saturday
    weekly = SimpleNamespace(trigger="schedule", frequency="weekly", day="Monday", time_of_day="08:00", timezone="UTC")
    daily = SimpleNamespace(trigger="schedule", frequency="daily", day=None, time_of_day="09:30", timezone="Asia/Dhaka")
    monthly = SimpleNamespace(trigger="schedule", frequency="monthly", day=None, time_of_day="08:00", timezone="UTC")
    assert next_run(weekly, after) == datetime(2026, 10, 26, 8, 0, tzinfo=UTC)  # type: ignore[arg-type]
    assert next_run(daily, after) == datetime(2026, 10, 25, 3, 30, tzinfo=UTC)  # 09:30 Dhaka = 03:30 UTC
    assert next_run(monthly, after) == datetime(2026, 11, 1, 8, 0, tzinfo=UTC)  # type: ignore[arg-type]


def test_due_automation_runs_once_per_slot(make_user: Any, engine: Any, owner_engine: Any) -> None:
    from app.engine import scheduler

    api = make_user()
    auto = api.post(
        "/automations",
        {
            "name": "Weekly SEO",
            "trigger": "schedule",
            "frequency": "weekly",
            "day": "Monday",
            "time": "08:00",
            "template": "SEO report",
            "tools": ["Agent analysis"],
            "file_scope": "No files",
            "approval_policy": "Ask before any change",
        },
    ).json()
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE automations SET next_run_at = now() - interval '1 minute' WHERE id = :a"), {"a": auto["id"]}
        )
    with engine.worker_factory() as db:
        assert scheduler.run_due_automations(db, engine.settings) == 1
    with engine.worker_factory() as db:
        assert scheduler.run_due_automations(db, engine.settings) == 0  # next_run_at moved forward
    tasks = api.get("/tasks").json()["items"]
    assert len(tasks) == 1 and tasks[0]["title"] == "Weekly SEO" and tasks[0]["status"] == "queued"
    engine.drain()
    assert api.get(f"/tasks/{tasks[0]['id']}").json()["status"] == "awaiting_approval"  # plans, never runs alone


def test_sandbox_cannot_unmount_its_masks_or_remount_root() -> None:
    """P3-SR-1 regression: masks must be locked, the program must hold no capabilities."""
    from app.core.config import get_settings
    from app.engine.sandbox import _hidden_paths, run_python

    _sandbox_or_skip()
    hidden = _hidden_paths(get_settings())
    code = (
        "import ctypes, os\n"
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        f"paths = {hidden!r}\n"
        "for p in paths:\n"
        "    r = libc.umount2(p.encode(), 2)\n"
        "    print('UMOUNTED' if r == 0 else 'denied', p)\n"
        "r = libc.mount(b'none', b'/', None, 32 | 4096, None)\n"  # MS_REMOUNT|MS_BIND
        "print('REMOUNT' if r == 0 else 'denied-remount')\n"
        "print('uid', os.getuid())\n"
        "for p in paths:\n"
        "    print('LEAK' if os.path.exists(p) and os.listdir(p) else 'empty', p)\n"
        "print(open('/proc/self/status').read().split('CapEff:')[1].split()[0])\n"
    )
    r = run_python(code)
    out = r.stdout
    assert "UMOUNTED" not in out and "REMOUNT\n" not in out and "LEAK" not in out, out
    assert out.strip().splitlines()[-1] == "0000000000000000", out  # no effective capabilities


def test_sandbox_output_flood_and_fork_bomb_are_contained() -> None:
    """P3-SR-6 regression."""
    import resource as _r

    from app.engine.sandbox import run_python

    _sandbox_or_skip()
    before = _r.getrusage(_r.RUSAGE_CHILDREN).ru_maxrss
    r = run_python("import sys\nwhile True: sys.stdout.write('x' * 1000000)")
    assert r.truncated is True and len(r.stdout) <= Settings().sandbox_max_output_bytes
    assert r.duration_ms < 5000  # killed as soon as the cap was exceeded, not at the timeout
    del before
    import os as _os

    if _os.geteuid() == 0:
        pytest.skip("RLIMIT_NPROC is not enforced for real root; the production worker runs as a non-root user")
    bomb = run_python(
        "import os\nn = 0\nfor _ in range(200):\n    try:\n        pid = os.fork()\n    except OSError:\n        break\n"
        "    if pid == 0:\n        import time; time.sleep(3); os._exit(0)\n    n += 1\nprint(n)"
    )
    assert int(bomb.stdout.strip() or 0) < 100, bomb


# ---------------------------------------------------------------- review regressions (P3-SR-4, P3-SR-9)
def test_only_the_owning_worker_can_finish_a_job(make_user: Any, engine: Any, owner_engine: Any) -> None:
    api = make_user()
    with engine.worker_factory() as db:
        Q.enqueue(db, workspace_id=_ws(api), kind="x", queue=JobQueue.tools, settings=engine.settings)
        db.commit()
        job = Q.claim(db, JobQueue.tools, "worker-A", engine.settings)
    assert job is not None
    with owner_engine.begin() as c:  # lease expired and another worker took it over
        c.execute(text("UPDATE jobs SET locked_by = 'worker-B' WHERE id = :j"), {"j": job.id})
    with engine.worker_factory() as db:
        assert Q.complete(db, job.id, "worker-A") is False
        assert Q.fail(db, job, "late", retryable=True, settings=engine.settings) == "lost"
        assert Q.still_owned(db, job) is False
        db.commit()
    with owner_engine.connect() as c:
        assert c.execute(text("SELECT status FROM jobs WHERE id = :j"), {"j": job.id}).scalar_one() == "running"


def test_stale_tool_job_owner_does_not_run_the_step(make_user: Any, engine: Any, site: Any, owner_engine: Any) -> None:
    from app.engine import runner
    from tests.conftest import set_workspace

    site.add("/", "<title>x</title>")
    api = make_user()
    set_workspace(owner_engine, api, allowed_domains=["127.0.0.1"], privacy_mode="approval")
    t = create_task(api, f"Inspect {site.url}/ and write a report.")
    engine.drain()
    t = api.get(f"/tasks/{t['id']}").json()
    api.post(f"/tasks/{t['id']}/plan/approve", {"selected_step_ids": [s["id"] for s in t["plan"]["steps"]]})
    engine.orchestrator.run_once()  # queues the tool job
    with engine.worker_factory() as db:
        job = Q.claim(db, JobQueue.tools, "worker-A", engine.settings)
    assert job is not None and job.kind == "tool.run"
    with owner_engine.begin() as c:
        c.execute(text("UPDATE jobs SET locked_by = 'worker-B' WHERE id = :j"), {"j": job.id})
    with engine.worker_factory() as db:
        runner.handle_tool_run(db, job, engine.settings, engine.worker_factory)
        db.commit()
    assert site.hits == []


def test_internal_follow_up_jobs_bypass_backpressure(make_user: Any, engine: Any) -> None:
    api = make_user()
    s = engine.settings.model_copy(update={"queue_max_pending_per_workspace": 0})
    with engine.worker_factory() as db:
        with pytest.raises(Q.QueueFull):
            Q.enqueue(db, workspace_id=_ws(api), kind="x", queue=JobQueue.tools, settings=s)
        assert Q.enqueue(db, workspace_id=_ws(api), kind="x", queue=JobQueue.tools, settings=s, internal=True)
        db.rollback()


def test_retry_reverifies_approval_except_consumption(make_user: Any, owner_engine: Any) -> None:
    from sqlalchemy import select

    from app.engine.policy import POLICY_VERSION
    from app.models import ApprovalRequest, DeletionRequest
    from app.services.approvals import deletion_extra, verify_binding
    from tests.conftest import upload

    api = make_user()
    f = upload(api, "r.csv").json()
    a = api.post(f"/files/{f['id']}/deletion-request", {"reason": "cleanup"}).json()
    api.post(
        f"/approvals/{a['id']}/decision", {"decision": "approve", "acknowledged": True, "confirmed_items": ["r.csv"]}
    )
    with owner_engine.begin() as c:
        c.execute(text("UPDATE approval_requests SET execution_state = 'executed' WHERE id = :a"), {"a": a["id"]})

    def check(**extra: Any) -> str | None:
        with Session(owner_engine) as db:
            row = db.get(ApprovalRequest, uuid.UUID(a["id"]))
            dr = db.execute(select(DeletionRequest).where(DeletionRequest.approval_id == row.id)).scalar_one()  # type: ignore[union-attr]
            return verify_binding(
                db,
                row,
                workspace_id=_ws(api),
                operation="deletion.execute",
                args_hash=row.args_hash,  # type: ignore[union-attr,arg-type]
                targets=list(row.targets),
                step_id=None,
                policy_version=POLICY_VERSION,  # type: ignore[union-attr]
                extra_hash=deletion_extra(dr),
                **extra,
            )

    assert check() == "approval was already used or invalidated"
    assert check(allow_consumed=True) is None
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE workspace_memberships SET role = 'member' WHERE user_id = :u"), {"u": api.me["user"]["id"]}
        )
    assert "no longer an owner" in (check(allow_consumed=True) or "")


def test_internal_errors_are_not_shown_to_users(
    make_user: Any, engine: Any, owner_engine: Any, monkeypatch: Any
) -> None:
    from app.engine import orchestrator

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("password=hunter2 at /srv/app/secret_module.py line 9")

    monkeypatch.setattr(orchestrator, "handle_plan", boom)
    api = make_user()
    t = create_task(api, "Write a report about nothing in particular please.")
    engine.drain()
    t = api.get(f"/tasks/{t['id']}").json()
    assert t["status"] == "failed"
    assert "hunter2" not in (t["error_message"] or "") and "secret_module" not in (t["error_message"] or "")
    assert "reference" in t["error_message"]
    with owner_engine.connect() as c:
        last = c.execute(
            text("SELECT last_error FROM jobs WHERE task_id = :t AND kind = 'task.plan'"), {"t": t["id"]}
        ).scalar_one()
    assert "RuntimeError" in last  # kept for operators


def test_slow_drip_response_hits_the_overall_deadline(engine: Any) -> None:
    """P3-SR-8 regression."""
    import time as _t

    class Slow(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            for _ in range(20):
                self.wfile.write(b"x")
                self.wfile.flush()
                _t.sleep(0.4)

        def log_message(self, *a: Any) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    s = engine.settings.model_copy(update={"egress_timeout_seconds": 1.0})
    pol = EgressPolicy(
        allowed_hosts=frozenset({"127.0.0.1"}), allow_private=True, allow_http=True, allowed_ports=frozenset({port})
    )
    started = _t.monotonic()
    from app.engine.egress import EgressFailed

    with pytest.raises(EgressFailed) as e:
        EgressClient(pol, s).get(f"http://127.0.0.1:{port}/")
    assert e.value.code == "timeout" and _t.monotonic() - started < 4
    srv.shutdown()


def test_answer_cannot_skip_a_pending_approval(make_user: Any, engine: Any, site: Any, owner_engine: Any) -> None:
    """P3-SR-7 regression."""
    from tests.conftest import set_workspace

    site.add("/", "<title>x</title>")
    api = make_user()
    set_workspace(owner_engine, api, allowed_domains=["127.0.0.1"], privacy_mode="strict")
    t = create_task(api, f"Inspect {site.url}/ and write a report.")
    engine.drain()
    t = api.get(f"/tasks/{t['id']}").json()
    api.post(f"/tasks/{t['id']}/plan/approve", {"selected_step_ids": [s["id"] for s in t["plan"]["steps"]]})
    engine.drain()
    assert api.get(f"/tasks/{t['id']}").json()["waiting_for"] == "approval"
    r = api.post(f"/tasks/{t['id']}/messages", {"text": "just continue", "kind": "answer"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "waiting_for_approval"


def test_shared_hosting_suffixes_cannot_be_allowlisted(make_user: Any) -> None:
    api = make_user()
    for d in ("github.io", "co.uk", "vercel.app"):
        assert api.patch("/workspace/settings", {"allowed_domains": [d]}).status_code == 422
    assert api.patch("/workspace/settings", {"allowed_domains": ["mysite.github.io"]}).status_code == 200


def test_storage_key_must_belong_to_the_workspace(make_user: Any, owner_engine: Any) -> None:
    """P3-SR-11: a row pointing at another tenant's storage prefix is never served."""
    from tests.conftest import upload

    a, b = make_user(), make_user()
    fa = upload(a, "a.csv").json()
    fb = upload(b, "b.csv").json()
    with owner_engine.begin() as c:
        key_b = c.execute(text("SELECT storage_key FROM files WHERE id = :f"), {"f": fb["id"]}).scalar_one()
        foreign = key_b.split("/")[0] + "/" + "e" * 32
        c.execute(text("UPDATE files SET storage_key = :k WHERE id = :f"), {"k": foreign, "f": fa["id"]})
    import shutil

    from app.core.config import get_settings

    root = get_settings().storage_dir
    shutil.copy(root / key_b, root / foreign)  # a real object exists under the other tenant's prefix
    r = a.get(f"/files/{fa['id']}/download")
    assert r.status_code == 410 and r.content != b"a,b\n1,2\n"
