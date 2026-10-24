# Performance and scalability

**No user capacity is promised.** Numbers below are measurements of one configuration on one machine, with the load generator on the same machine. They are a starting point for sizing, not proof of production capacity.

## 1. Test environment (2026-10-24)

| Item | Value |
|---|---|
| Machine | 4 vCPU Intel Xeon @ 2.8 GHz, 16 GB RAM (comparable to a Hostinger KVM 4) — shared by API, Postgres, workers **and the load generator** |
| PostgreSQL | 16.15, `max_connections=100`, default tuning, separate `steward_perf` database |
| API | 2 uvicorn processes, DB pool 10 + 10 overflow per process, pool timeout 5 s, `--limit-concurrency 80` |
| Workers | 1 each: orchestrator, tools, deletion, scheduler (concurrency 2) |
| Model | `MODEL_PROVIDER=mock` (deterministic, no network) — real model latency is **not** included |
| Rate limiting | **Disabled** for capacity measurement (production keeps it on; its behaviour is covered by tests) |
| Data | 20 users, ~6,000 tasks, ~24,000 audit events at the end |
| Tool | `perf/loadtest.py` (httpx + asyncio), resource sampler `perf/sample.sh`; raw results in `perf/results/` |

**Workload model** per virtual user (VU), think time 0.2–1.0 s: dashboard 20 %, task list 20 %, task detail 15 %, approvals 10 %, files 10 %, notifications 10 %, agent status 5 %, create task 10 %. A VU is a tight loop, not a person: 1 VU ≈ several active humans.

## 2. Provisional SLOs (configurable in `perf/slo.json`)

| SLO | Target | Rationale |
|---|---|---|
| Read latency p95 / p99 | ≤ 300 ms / ≤ 800 ms | Pages make several API calls; this keeps screens under ~1 s on a typical connection |
| Write latency p95 | ≤ 600 ms | Task creation includes validation, audit and job enqueue in one transaction |
| Error rate (5xx + timeouts) | ≤ 1 % per test window | Fast `503 service_busy` shedding counts as errors |
| Plan wait p95 (queue) | ≤ 30 s | Planning is asynchronous; users see progress, but long waits feel broken |
| Availability | 99.5 % monthly | Realistic for a single VPS with planned restarts (≈ 3.6 h/month) |

## 3. Results

| Scenario | VUs | req/s | read p50 | read p95 | read p99 | write p95 | errors | SLO |
|---|---|---|---|---|---|---|---|---|
| Baseline (60 s) | 10 | 16.0 | 13 ms | 25 ms | 147 ms | 35 ms | 0 % | **PASS** |
| Step 1 (45 s) | 10 | 16.0 | 15 ms | 31 ms | 438 ms | 48 ms | 0 % | PASS |
| Step 2 | 25 | 39.1 | 16 ms | 39 ms | 582 ms | 55 ms | 0 % | PASS |
| Step 3 | 50 | 74.3 | 21 ms | 143 ms | 1,089 ms | 162 ms | 0 % | FAIL (p99) |
| Step 4 | 100 | 113.4 | 150 ms | 521 ms | 2,411 ms | 710 ms | 0.76 % (all fast 503) | FAIL (latency) |
| Spike (5 → 150 → 5) | 150 | 83.3 | 476 ms | 6,891 ms | 11,393 ms | 6,986 ms | 2.5 % (all fast 503) | FAIL during spike; **recovered immediately** (PASS after) |
| Soak (10 min) | 20 | 32.3 | 16 ms | 33 ms | 53 ms | 52 ms | 0 % of 19,365 | **PASS** — API RSS flat (302 MB), DB connections flat (26) |
| Queue saturation | 300 tasks in 17 s | — | — | — | — | — | 0 rejected | all planned. **Corrected in Phase 5:** the 37 s "p95" was the load tool's own polling loop, not queue wait; measured server-side, plan wait p95 is **1.7 s** with 1 orchestrator (§3a) |
| Worker failure (orchestrator SIGKILL, restart after 10 s) | 60 tasks | — | — | — | — | — | 0 lost | all planned, p95 20 s incl. outage. Lease recovery of an in-flight job is covered by `test_stale_running_job_is_recovered` (no job was in flight at the kill) |
| API restart under load (graceful) | 10 | 16.3 | 15 ms | 29 ms | 137 ms | 53 ms | 3 % (29 refused connections) | 2.4 s unavailable with one API instance — **fixed in Phase 5** by two API containers + rolling restart (§3b) |

### 3a. Queue wait vs. orchestrator replicas (Phase 5, 2026-10-24)

Same machine and perf database; `perf/orchestrator_scaling.sh` (1, 2, 4 orchestrator processes, one
tools worker, worker concurrency 2 each). Plan wait = task created → plan ready, read **server-side**
from each task's event log (`loadtest.py queue --db`), so polling delay is not included.

**Correction of the Phase 4 result.** Phase 4 reported "plan wait p95 37 s — FAIL". The load tool polled
300 tasks one at a time with a new connection each, so it mostly measured its own loop. The tool now polls
concurrently with reused connections and reads exact server-side timings. With the instant mock model the
queue was never the bottleneck:

| Burst | Model | Orchestrators | Server plan wait p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|
| 300 tasks (accepted in ~17 s) | mock, instant | 1 | 1.03 s | **1.70 s** | 1.84 s | 1.98 s |
| 300 tasks | mock, instant | 2 | 0.84 s | 1.18 s | 1.25 s | 1.28 s |
| 300 tasks | mock, instant | 4 | 0.69 s | 1.17 s | 1.34 s | 1.46 s |
| 120 tasks (accepted in ~6 s) | mock with **3 s latency** (`MOCK_LATENCY_MS=3000`) | 1 | 89.8 s | **167.8 s** | 176.8 s | 176.8 s |
| 120 tasks | 3 s latency | 2 | 43.4 s | **82.2 s** | 85.4 s | 85.4 s |
| 120 tasks | 3 s latency | 4 | 19.5 s | **38.8 s** | 39.8 s | 39.8 s |

With a realistic model latency, throughput scales almost linearly with replicas
(≈ replicas × concurrency ÷ model latency plans per second). Default is now `ORCHESTRATOR_REPLICAS=2`.
Rule of thumb for the 30 s plan-wait SLO: replicas ≥ burst size × model latency ÷ (2 × 30 s).
Real model latency, rate limits and cost were **not** measured (no provider in this environment).

### 3b. Restarts under load: one vs. two API containers (Phase 5 production rehearsal)

Production-mode Docker stack through Caddy (TLS), 10 signed-in sessions, ~125 req/s (GETs + 10 % task
creation), `api` and `api-2` with one uvicorn process each:

| Scenario | Requests | Errors (5xx / refused) | Longest gap without a successful response |
|---|---|---|---|
| Baseline (30 s) | 3,772 | 0 | 0.07 s |
| `deploy/rolling-restart.sh` during 100 s of load | 12,285 | **0** | **0.09 s** |
| `docker compose restart api api-2` (both at once) | 6,194 | 0 | **10.1 s** (Caddy held requests up to `lb_try_duration` until an API returned) |
| Phase 4: single API container, graceful restart | — | 3 % refused | 2.4 s |

Use `deploy/rolling-restart.sh` for releases. Migrations must be backward compatible while two
versions run side by side for a few seconds (add columns first, remove them in a later release).

Resource peaks during the 100-VU step: API ≈ 0.7 CPU, Postgres ≈ 0.5 CPU, load average 4.8 (the box was saturated, partly by the load generator), DB connections 36.

## 4. Findings and changes made

1. **DB connection exhaustion (fixed).** First 100-VU run: p95 30 s and 4.9 % timeouts while CPU was low — requests waited 30 s for a pool connection (pool 5 + 10). Fix: configurable pool (`DB_POOL_SIZE=10`, `DB_MAX_OVERFLOW=10`), `DB_POOL_TIMEOUT_SECONDS=5`, pool timeouts return `503 service_busy` with `Retry-After` (test: `test_database_pool_exhaustion_returns_503_quickly`), and `API_MAX_CONCURRENCY` sheds excess requests per process. Result at 100 VUs: p95 30,328 ms → 521 ms, throughput 41 → 113 req/s, errors only fast 503s.
2. **Uneven load across uvicorn processes (addressed in Phase 5).** With keep-alive clients one process served most requests (63 % CPU vs ~5 %). The stack now runs two single-process API containers behind Caddy (`lb_policy round_robin`) instead of more `--workers`.
3. **Index review.** All hot queries use indexes and run < 1 ms at this size. Added migration `0006`: `audit_events(workspace_id, seq)`, `task_events(task_id, seq)`, a partial index for claiming pending jobs, `jobs(workspace_id, status)` for backpressure counts.
4. **Queue throughput.** See §3a: with a real model, planning time is dominated by model latency; scale with `ORCHESTRATOR_REPLICAS` (claims use `SKIP LOCKED`).

## 5. Implemented safeguards

Rate limits (auth, writes, uploads, task creation, denied-ID probes) · per-workspace quotas (tasks, storage, model tokens/calls) · bounded worker concurrency and per-workspace running cap · queue backpressure (`503 queue_full`) · pagination on every list endpoint (`page_size` ≤ 100/200) · DB statement timeout 15 s · pool timeout 5 s · uvicorn keep-alive/graceful-shutdown/concurrency limits · Caddy header/body/response timeouts and 30 MB body cap · `Cache-Control: no-store` on all API responses (tested), long caching only for content-hashed `/_next/static/*` · `/livez`, `/readyz`, token-protected `/metrics`.

## 6. Not measured (explicitly)

- Real model provider latency, cost and rate limiting under load (gateway retry/429 behaviour is covered by unit tests: `test_openai_compatible_adapter_against_a_local_server`, `test_gateway_retries_transient_errors_and_records_usage`).
- The Next.js web tier and Caddy under load (only the API was loaded directly).
- Long soak (hours/days), multi-node, or anything near large user counts. **No claim of supporting thousands or millions of concurrent users is made.**

## 7. Reproduce

```bash
# isolated database + API on :8100 (rate limits off, mock model), workers on the same DB — see the report for env
uv run --project api python perf/loadtest.py setup --users 20 --outbox api/var/perf-outbox
uv run --project api python perf/loadtest.py run --scenario baseline|step|spike|soak --out perf/results/<name>.json
uv run --project api python perf/loadtest.py queue --tasks 300 --db "$DATABASE_URL"   # exact server-side waits
PERF_ENV=<perf env file> perf/orchestrator_scaling.sh 300                               # 1/2/4 orchestrators
perf/sample.sh <seconds> perf/results/<name>-resources.csv
```
Never run these against production.

## 8. When a single VPS stops being enough

| Signal | Next step |
|---|---|
| p95 > SLO at normal load, API CPU saturated | Run 2–4 single-process API containers behind Caddy; move the load generator/other services off the box |
| Postgres CPU > 70 % or connections near `max_connections` | Managed PostgreSQL (or a dedicated DB VPS) + PgBouncer; read replica for reporting |
| Plan wait p95 > 30 s (`steward_queue_oldest_pending_seconds`) | Raise `ORCHESTRATOR_REPLICAS` (§3a); if the jobs table becomes hot (> ~50 jobs/s sustained), a dedicated queue (e.g. Redis Streams/RabbitMQ/SQS) |
| Storage > ~100 GB, or more than one app node | External private object storage (S3-compatible) with server-side encryption |
| Availability target > 99.5 % | Zero-downtime API restarts already work on one server (§3b); beyond that: two app nodes behind a load balancer, managed DB with failover |
