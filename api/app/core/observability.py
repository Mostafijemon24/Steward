"""Liveness, readiness and Prometheus-format metrics.

Nothing here contains task content, prompts, file names, emails or workspace identifiers:
only aggregate counts, latencies and states. Metrics stay on the server unless an operator
deliberately scrapes them (see docs/OPERATIONS.md).

- GET /livez   — the process is up (no dependencies).
- GET /readyz  — the database answers, migrations are at head, storage is writable.
- GET /healthz — same as /readyz (kept for existing healthchecks).
- GET /metrics — Prometheus text format; requires `Authorization: Bearer $METRICS_TOKEN`
  and is disabled when METRICS_TOKEN is unset. Not routed by the web app or Caddy.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import text
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.db import get_engine

BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class _Metrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self.hist: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0] * (len(BUCKETS) + 1))
        self.sums: dict[tuple[str, str], float] = defaultdict(float)
        self.in_flight = 0
        self.started = time.time()

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        cls = f"{status // 100}xx"
        with self.lock:
            self.requests[(method, route, cls)] += 1
            h = self.hist[(method, route)]
            for i, b in enumerate(BUCKETS):
                if seconds <= b:
                    h[i] += 1
                    break
            else:
                h[-1] += 1
            self.sums[(method, route)] += seconds


METRICS = _Metrics()


class MetricsMiddleware:
    """Counts requests by route template (never the raw path, which contains IDs)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = 500

        async def wrap(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        with METRICS.lock:
            METRICS.in_flight += 1
        try:
            await self.app(scope, receive, wrap)
        finally:
            with METRICS.lock:
                METRICS.in_flight -= 1
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            METRICS.observe(scope.get("method", "GET"), template, status, time.perf_counter() - start)


@lru_cache(maxsize=1)
def migration_head() -> str | None:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        ini = Path(__file__).resolve().parents[2] / "alembic.ini"
        cfg = Config(str(ini))
        cfg.set_main_option("script_location", str(ini.parent / "migrations"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:
        return None


def readiness(include_optional: bool = True) -> tuple[bool, dict[str, str]]:
    """Core checks (database, migrations, storage) plus, for /readyz, optional services (malware scanner).

    The container healthcheck (/healthz) uses core checks only: an optional dependency being down must
    not mark the API unhealthy (uploads already fail closed on their own when the scanner is down).
    """
    checks: dict[str, str] = {}
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
            current = c.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        checks["database"] = "ok"
        head = migration_head()
        checks["migrations"] = "ok" if head is None or current == head else "pending"
    except Exception:
        checks["database"] = "unavailable"
    storage = get_settings().storage_dir
    try:
        storage.mkdir(parents=True, exist_ok=True)
        probe = storage / ".ready-probe"
        probe.write_bytes(b"ok")
        probe.unlink()
        checks["storage"] = "ok"
    except OSError:
        checks["storage"] = "unwritable"
    if include_optional and get_settings().malware_scanner == "clamav":
        from app.services.files import get_scanner

        scanner = get_scanner()
        checks["malware_scanner"] = "ok" if getattr(scanner, "ping", lambda: False)() else "unavailable"
    return all(v == "ok" for v in checks.values()), checks


def _engine_metrics() -> list[str]:
    s = get_settings()
    out: list[str] = []
    pool: Any = get_engine().pool
    for name, fn in (("checked_out", "checkedout"), ("size", "size"), ("overflow", "overflow")):
        f = getattr(pool, fn, None)
        if callable(f):
            out.append(f"steward_db_pool_{name} {f()}")
    try:
        with get_engine().connect() as c:
            for queue, status, n in c.execute(
                text("SELECT queue, status, count(*) FROM jobs GROUP BY queue, status")
            ).tuples():
                out.append(f'steward_jobs{{queue="{queue}",status="{status}"}} {n}')
            oldest = c.execute(
                text(
                    "SELECT queue, extract(epoch FROM now() - min(run_after)) FROM jobs "
                    "WHERE status = 'pending' AND run_after <= now() GROUP BY queue"
                )
            ).tuples()
            for queue, age in oldest:
                out.append(f'steward_queue_oldest_pending_seconds{{queue="{queue}"}} {float(age or 0):.1f}')
            for kind, alive in c.execute(
                text(
                    "SELECT kind, count(*) FILTER (WHERE heartbeat_at > now() - interval '60 seconds') "
                    "FROM workers WHERE stopped_at IS NULL GROUP BY kind"
                )
            ).tuples():
                out.append(f'steward_workers_alive{{kind="{kind}"}} {alive}')
            for status, n in c.execute(text("SELECT status, count(*) FROM tasks GROUP BY status")).tuples():
                out.append(f'steward_tasks{{status="{status}"}} {n}')
            for outcome, loc, n, lat in c.execute(
                text(
                    "SELECT outcome, location, count(*), coalesce(avg(latency_ms), 0) FROM model_calls "
                    "WHERE created_at > now() - interval '15 minutes' GROUP BY outcome, location"
                )
            ).tuples():
                out.append(f'steward_model_calls_15m{{outcome="{outcome}",location="{loc}"}} {n}')
                out.append(f'steward_model_latency_ms_avg_15m{{outcome="{outcome}",location="{loc}"}} {float(lat):.0f}')
            for action, n in c.execute(
                text(
                    "SELECT action, count(*) FROM audit_events WHERE created_at > now() - interval '15 minutes' "
                    "AND action IN ('policy.blocked','authz.denied','authz.role_denied','approval.binding_failed',"
                    "'auth.login_failed','auth.mfa_failed','file.quarantined','file.deletion_refused','step.failed',"
                    "'task.finished') GROUP BY action"
                )
            ).tuples():
                out.append(f'steward_audit_events_15m{{action="{action}"}} {n}')
    except Exception:
        out.append("steward_metrics_db_error 1")
    out.append(f"steward_queue_capacity_total {s.queue_max_pending_total}")
    return out


def render_metrics() -> str:
    lines = [
        "# Steward metrics (aggregate only; no task content or identifiers)",
        f"steward_process_uptime_seconds {time.time() - METRICS.started:.0f}",
        f"steward_http_in_flight {METRICS.in_flight}",
        f'steward_process_info{{pid="{os.getpid()}"}} 1',
    ]
    with METRICS.lock:
        for (m, r, c), n in sorted(METRICS.requests.items()):
            lines.append(f'steward_http_requests_total{{method="{m}",route="{r}",status="{c}"}} {n}')
        for (m, r), h in sorted(METRICS.hist.items()):
            cum = 0
            for b, v in zip((*BUCKETS, float("inf")), h, strict=True):
                cum += v
                le = "+Inf" if b == float("inf") else str(b)
                lines.append(f'steward_http_request_seconds_bucket{{method="{m}",route="{r}",le="{le}"}} {cum}')
            lines.append(f'steward_http_request_seconds_sum{{method="{m}",route="{r}"}} {METRICS.sums[(m, r)]:.4f}')
            lines.append(f'steward_http_request_seconds_count{{method="{m}",route="{r}"}} {cum}')
    lines.extend(_engine_metrics())
    return "\n".join(lines) + "\n"


def install(app: FastAPI) -> None:
    @app.get("/livez", include_in_schema=False)
    def livez() -> dict[str, str]:
        return {"status": "ok"}

    def _report(include_optional: bool) -> Response:
        ok, checks = readiness(include_optional)
        return JSONResponse({"status": "ok" if ok else "unavailable", "checks": checks}, status_code=200 if ok else 503)

    app.add_api_route("/readyz", lambda: _report(True), methods=["GET"], include_in_schema=False)
    app.add_api_route("/healthz", lambda: _report(False), methods=["GET"], include_in_schema=False)

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request) -> Response:
        token = get_settings().metrics_token
        if token is None or not token.get_secret_value():
            return JSONResponse({"error": {"code": "not_found", "message": "Not found."}}, status_code=404)
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.get_secret_value().encode()):
            return JSONResponse({"error": {"code": "not_found", "message": "Not found."}}, status_code=404)
        return PlainTextResponse(render_metrics(), media_type="text/plain; version=0.0.4")
