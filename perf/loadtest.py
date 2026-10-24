"""Reproducible HTTP load test for the Steward API (no extra dependencies: httpx + asyncio).

Run ONLY against a test/staging environment you are authorised to load, never production.

  uv run --project api python perf/loadtest.py setup    --base http://127.0.0.1:8100 --users 20 --outbox api/var/perf-outbox
  uv run --project api python perf/loadtest.py run      --base http://127.0.0.1:8100 --scenario baseline
  scenarios: baseline | step | spike | soak | writes
  uv run --project api python perf/loadtest.py queue    --base http://127.0.0.1:8100 --tasks 300

Workload model (per virtual user, think time 0.2–1.0 s): dashboard 20%, task list 20%, task detail 15%,
approvals 10%, files 10%, notifications 10%, agent status 5%, create task 10% (writes). A virtual
user is a tight loop with think time, NOT a human: 100 VUs ≈ several hundred active humans.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
SESSIONS = HERE / ".sessions.json"


@dataclass
class Stats:
    lat: dict[str, list[float]] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)
    codes: dict[int, int] = field(default_factory=dict)

    def add(self, kind: str, ms: float, code: int) -> None:
        self.lat.setdefault(kind, []).append(ms)
        self.codes[code] = self.codes.get(code, 0) + 1
        if code >= 500 or code == 0:
            self.errors[kind] = self.errors.get(kind, 0) + 1


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))
    return xs[k]


async def csrf(c: httpx.AsyncClient) -> str:
    await c.get("/api/v1/auth/csrf")
    return c.cookies.get("stw_csrf") or ""


async def setup(base: str, users: int, outbox: Path, origin: str) -> None:
    sessions = []
    async with httpx.AsyncClient(base_url=base, headers={"Origin": origin}, timeout=30) as anon:
        for i in range(users):
            email = f"perf-{int(time.time())}-{i}@example.com"
            c = httpx.AsyncClient(base_url=base, headers={"Origin": origin}, timeout=30)
            tok = await csrf(c)
            r = await c.post("/api/v1/auth/register", json={"name": f"Perf {i}", "email": email, "password": "perf-pass-1234", "accept_terms": True}, headers={"X-CSRF-Token": tok})
            assert r.status_code == 202, r.text
            await asyncio.sleep(0.6)
            link = None
            for f in sorted(outbox.glob("*.eml"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
                raw = f.read_text()
                if f"To: {email}" in raw:
                    link = re.search(r"verify-email\?token=([A-Za-z0-9_-]+)", raw)
                    break
            assert link, f"no verification mail for {email}"
            tok = await csrf(c)
            await c.post("/api/v1/auth/verify-email", json={"token": link.group(1)}, headers={"X-CSRF-Token": tok})
            tok = await csrf(c)
            r = await c.post("/api/v1/auth/login", json={"email": email, "password": "perf-pass-1234"}, headers={"X-CSRF-Token": tok})
            assert r.status_code == 200, r.text
            tok = await csrf(c)
            # a little data per user
            ids = []
            for _ in range(3):
                t = await c.post("/api/v1/tasks", json={"prompt": "Write a short report about our launch plan please.", "type": "research"}, headers={"X-CSRF-Token": tok})
                ids.append(t.json()["id"])
            sessions.append({"cookies": dict(c.cookies), "task_ids": ids})
            await c.aclose()
    SESSIONS.write_text(json.dumps(sessions))
    print(f"setup: {len(sessions)} signed-in users with 3 tasks each")


READS = [
    ("dashboard", "/api/v1/dashboard", 20),
    ("task_list", "/api/v1/tasks?page_size=25", 20),
    ("task_detail", None, 15),
    ("approvals", "/api/v1/approvals?status=pending", 10),
    ("files", "/api/v1/files", 10),
    ("notifications", "/api/v1/notifications", 10),
    ("agent_status", "/api/v1/agent/status", 5),
]


async def vu(base: str, origin: str, sess: dict, stats: Stats, stop_at: float, write_ratio: float) -> None:
    async with httpx.AsyncClient(base_url=base, headers={"Origin": origin}, cookies=sess["cookies"], timeout=30) as c:
        tok = sess["cookies"].get("stw_csrf", "")
        while time.monotonic() < stop_at:
            if random.random() < write_ratio:  # noqa: S311
                kind, method, url = "create_task", "POST", "/api/v1/tasks"
            else:
                kind, url, _w = random.choices(READS, weights=[w for *_x, w in READS])[0]  # noqa: S311
                method = "GET"
                if url is None:
                    url = f"/api/v1/tasks/{random.choice(sess['task_ids'])}"  # noqa: S311
            t0 = time.perf_counter()
            try:
                if method == "GET":
                    r = await c.get(url)
                else:
                    r = await c.post(url, json={"prompt": "Write a short report about our launch plan please.", "type": "research"}, headers={"X-CSRF-Token": tok})
                code = r.status_code
            except httpx.HTTPError:
                code = 0
            stats.add(kind, (time.perf_counter() - t0) * 1000, code)
            await asyncio.sleep(random.uniform(0.2, 1.0))  # noqa: S311


async def stage(base: str, origin: str, sessions: list, vus: int, seconds: float, write_ratio: float) -> Stats:
    stats = Stats()
    stop = time.monotonic() + seconds
    await asyncio.gather(*(vu(base, origin, sessions[i % len(sessions)], stats, stop, write_ratio) for i in range(vus)))
    return stats


def report(name: str, vus: int, seconds: float, s: Stats, slo: dict) -> dict:
    reads = [x for k, v in s.lat.items() if k != "create_task" for x in v]
    writes = s.lat.get("create_task", [])
    total = sum(len(v) for v in s.lat.values())
    errors = sum(s.errors.values())
    rate_limited = s.codes.get(429, 0) + s.codes.get(503, 0)
    row = {
        "stage": name,
        "vus": vus,
        "seconds": seconds,
        "requests": total,
        "rps": round(total / seconds, 1),
        "read_p50_ms": round(pct(reads, 50), 1),
        "read_p95_ms": round(pct(reads, 95), 1),
        "read_p99_ms": round(pct(reads, 99), 1),
        "write_p95_ms": round(pct(writes, 95), 1),
        "error_rate": round(errors / total, 4) if total else 0,
        "throttled_429_503": rate_limited,
        "codes": dict(sorted(s.codes.items())),
        "p95_by_endpoint_ms": {k: round(pct(v, 95), 1) for k, v in sorted(s.lat.items())},
    }
    ok = (
        row["read_p95_ms"] <= slo["read_p95_ms"]
        and row["read_p99_ms"] <= slo["read_p99_ms"]
        and (not writes or row["write_p95_ms"] <= slo["write_p95_ms"])
        and row["error_rate"] <= slo["error_rate_max"]
    )
    row["slo"] = "PASS" if ok else "FAIL"
    print(json.dumps(row))
    return row


SCENARIOS = {
    "baseline": [(10, 60)],
    "step": [(10, 45), (25, 45), (50, 45), (100, 45)],
    "spike": [(5, 20), (150, 30), (5, 20)],
    "soak": [(20, 600)],
    "writes": [(20, 60)],
    "probe50": [(50, 45)],
}


async def run(base: str, origin: str, scenario: str, slo: dict, out: Path | None) -> None:
    sessions = json.loads(SESSIONS.read_text())
    rows = []
    for vus, secs in SCENARIOS[scenario]:
        wr = 0.5 if scenario == "writes" else 0.1
        s = await stage(base, origin, sessions, vus, secs, wr)
        rows.append(report(f"{scenario}:{vus}vu", vus, secs, s, slo))
    if out:
        out.write_text(json.dumps(rows, indent=2))


async def queue_saturation(base: str, origin: str, tasks: int, db_url: str | None = None) -> None:
    """Create many tasks quickly; report admission (201 vs 429/503) and time until all are planned."""
    sessions = json.loads(SESSIONS.read_text())
    created: list[tuple[dict, str]] = []
    codes: dict[int, int] = {}
    t0 = time.monotonic()

    async def one(i: int) -> None:
        sess = sessions[i % len(sessions)]
        async with httpx.AsyncClient(base_url=base, headers={"Origin": origin}, cookies=sess["cookies"], timeout=30) as c:
            r = await c.post("/api/v1/tasks", json={"prompt": "Write a short report about our launch plan please.", "type": "research"}, headers={"X-CSRF-Token": sess["cookies"].get("stw_csrf", "")})
            codes[r.status_code] = codes.get(r.status_code, 0) + 1
            if r.status_code == 201:
                created.append((sess, r.json()["id"]))

    sem = asyncio.Semaphore(50)

    async def guarded(i: int) -> None:
        async with sem:
            await one(i)

    await asyncio.gather(*(guarded(i) for i in range(tasks)))
    admitted = time.monotonic() - t0
    # Poll concurrently with one reused client per session. (The first version polled one task at a
    # time with a new connection each, so its "wait" mostly measured its own polling loop.)
    clients: dict[int, httpx.AsyncClient] = {}

    def client_for(sess: dict) -> httpx.AsyncClient:
        key = id(sess)
        if key not in clients:
            clients[key] = httpx.AsyncClient(base_url=base, headers={"Origin": origin}, cookies=sess["cookies"], timeout=30)
        return clients[key]

    waits: list[float] = []
    pending = list(created)
    deadline = time.monotonic() + 600
    poll_sem = asyncio.Semaphore(40)

    async def check(sess: dict, tid: str) -> bool:
        async with poll_sem:
            st = (await client_for(sess).get(f"/api/v1/tasks/{tid}")).json()["status"]
        return st in ("awaiting_approval", "failed")

    while pending and time.monotonic() < deadline:
        done = await asyncio.gather(*(check(s, t) for s, t in pending))
        now = time.monotonic() - t0
        waits.extend(now for d in done if d)
        pending = [p for p, d in zip(pending, done, strict=True) if not d]
        if pending:
            await asyncio.sleep(0.5)
    for c in clients.values():
        await c.aclose()
    result: dict = {
        "scenario": "queue_saturation",
        "submitted": tasks,
        "codes": codes,
        "admission_seconds": round(admitted, 1),
        "planned": len(waits),
        "not_planned_within_600s": len(pending),
        "client_observed_done_p50_s_from_burst_start": round(statistics.median(waits), 1) if waits else None,
        "client_observed_done_p95_s_from_burst_start": round(pct(waits, 95), 1) if waits else None,
    }
    if db_url:
        result.update(server_side_waits(db_url, [tid for _s, tid in created]))
    print(json.dumps(result))


def server_side_waits(db_url: str, task_ids: list[str]) -> dict:
    """Exact queue wait per task: task created → plan ready, from the task's own event log (read-only)."""
    from sqlalchemy import create_engine, text

    eng = create_engine(db_url)
    with eng.connect() as c:
        rows = c.execute(
            text(
                "SELECT extract(epoch FROM e.created_at - t.created_at) FROM tasks t JOIN task_events e "
                "ON e.task_id = t.id AND e.kind = 'task.status_changed' AND e.to_status = 'awaiting_approval' "
                "WHERE t.id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": task_ids},
        ).scalars().all()
    eng.dispose()
    w = [float(x) for x in rows]
    return {
        "server_plan_wait_p50_s": round(pct(w, 50), 2) if w else None,
        "server_plan_wait_p95_s": round(pct(w, 95), 2) if w else None,
        "server_plan_wait_p99_s": round(pct(w, 99), 2) if w else None,
        "server_plan_wait_max_s": round(max(w), 2) if w else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["setup", "run", "queue"])
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--origin", default="http://localhost:3000")
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--outbox", default="api/var/perf-outbox")
    ap.add_argument("--scenario", default="baseline", choices=list(SCENARIOS))
    ap.add_argument("--tasks", type=int, default=300)
    ap.add_argument("--out")
    ap.add_argument("--db", help="read-only DB URL of the TEST database for exact server-side queue waits")
    a = ap.parse_args()
    slo = json.loads((HERE / "slo.json").read_text())
    if a.cmd == "setup":
        asyncio.run(setup(a.base, a.users, Path(a.outbox), a.origin))
    elif a.cmd == "run":
        asyncio.run(run(a.base, a.origin, a.scenario, slo, Path(a.out) if a.out else None))
    else:
        asyncio.run(queue_saturation(a.base, a.origin, a.tasks, a.db))


if __name__ == "__main__":
    main()
