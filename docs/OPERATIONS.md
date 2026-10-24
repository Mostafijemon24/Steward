# Operations: health, metrics, alerts and telemetry

## 1. Telemetry policy: what leaves the server

**By default nothing leaves the server** except:

| Data | Destination | When | Content |
|---|---|---|---|
| Model requests | the configured `MODEL_BASE_URL` | only if `MODEL_PROVIDER=openai_compatible` and the task's data-sharing rules allow it | redacted task context the user approved; shown in the UI before sending |
| Allowlisted web reads | domains on the workspace allowlist | tools worker, approved steps only | HTTP GET requests; the requested URL is visible to that site |
| E-mail | your SMTP server | verification, password reset, notifications the user enabled | recipient, subject, a link; no task content |
| TLS certificates | Let's Encrypt (ACME) | certificate issue/renewal | the domain name |
| Docker image pulls / OS updates | Docker Hub, Ubuntu mirrors | on build/upgrade | none |
| Malware signatures (if enabled) | database.clamav.net (freshclam container only) | every 2 hours | download only; no file content or metadata is uploaded |
| WordPress draft posts (if enabled) | your WordPress site | approved steps only | the approved title/text and the Application Password (HTTPS, Basic auth) |

There is **no** analytics, error-reporting SaaS, APM agent or log shipper in the stack. If you add
one (Sentry, Grafana Cloud, Datadog…), send **aggregate metrics only** (the `/metrics` output
below is designed for that). Do not ship application logs, request bodies, task content, file
contents, prompts or model responses to a third party. Update this table when you do.

What the logs contain: client address, method, full path **including the query string**, and status
(uvicorn and Caddy access logs; list filters such as search terms can therefore appear in logs),
worker job ids and outcomes, audit action names. Request bodies are not logged. Secrets are never
logged; the redaction tests (`test_redaction_removes_common_secrets`) cover model and tool output.
Logs stay in Docker's json-file driver on the server (5 × 10 MB per container).

## 2. Endpoints (internal network only)

Caddy returns 404 for all of these from the internet. Reach them with
`docker compose exec api python -c "..."` or from a monitoring container attached to the
`backend` network.

| Endpoint | Purpose | Auth |
|---|---|---|
| `/livez` | process is up (no dependencies) | none |
| `/readyz` | DB reachable, migrations at head, storage writable; 503 + reason otherwise | none |
| `/healthz` | used by the container healthcheck | none |
| `/metrics` | Prometheus text format | `Authorization: Bearer $METRICS_TOKEN`; **404 when the token is unset or wrong** |

Workers have no HTTP port. Their health is their heartbeat row (container healthcheck
`python -m app.workers.health`) and `steward_workers_alive` below.

## 3. Metrics (`/metrics`)

All values are aggregates: no user ids, workspace ids, e-mails, task text or file names.

| Metric | Meaning |
|---|---|
| `steward_http_requests_total{method,route,status}` | requests by route template (e.g. `/tasks/{task_id}`) |
| `steward_http_request_seconds_bucket/_sum/_count{route}` | latency histogram |
| `steward_http_in_flight` | concurrent requests in this process |
| `steward_db_pool_checked_out`, `_size`, `_overflow` | DB connection pool use (this process) |
| `steward_jobs{queue,status}` | queue depth by state (pending, running, done, failed…) |
| `steward_queue_oldest_pending_seconds{queue}` | age of the oldest runnable job = queue wait |
| `steward_queue_capacity_total` | backpressure limit (`503 queue_full` above it) |
| `steward_workers_alive{kind}` | workers with a heartbeat in the last 60 s |
| `steward_tasks{status}` | tasks by status |
| `steward_model_calls_15m{outcome,location}` | model calls in the last 15 min, by recorded outcome |
| `steward_model_latency_ms_avg_15m{outcome,location}` | average model latency |
| `steward_audit_events_15m{action}` | security-relevant audit actions in the last 15 min: `policy.blocked`, `authz.denied`, `authz.role_denied`, `approval.binding_failed`, `auth.login_failed`, `auth.mfa_failed`, `file.quarantined`, `file.deletion_refused`, `step.failed`, `task.finished` |
| `steward_metrics_db_error` | 1 if the DB-derived metrics could not be read |
| `steward_process_info{pid}`, `steward_process_uptime_seconds` | process identity / restarts |

**Limitation:** request counters, latency and pool metrics are **per uvicorn process**. With
`API_PROCESSES=2` a scrape reaches one process at random. Database-derived metrics (jobs, queue
age, workers, tasks, model calls, audit) are global and exact. If you need exact request metrics,
run `API_PROCESSES=1` (and more API containers, see docs/PERFORMANCE.md) or aggregate by `pid`.

## 4. Suggested alerts

Thresholds come from the provisional SLOs in `perf/slo.json`; tune them with real traffic.

| Alert | Condition (for 5 min unless noted) | Likely action |
|---|---|---|
| Site down | external HTTPS check of `/login` fails 2× in a row | `docker compose ps`, Caddy/web logs |
| Not ready | `/readyz` ≠ 200 | DB down, migrations pending, disk full |
| Worker missing | `steward_workers_alive{kind=…} == 0` | worker logs; `docker compose up -d` |
| Queue wait | `steward_queue_oldest_pending_seconds > 30` | add orchestrator replicas; check model provider |
| Queue near full | sum of pending jobs > 80 % of `steward_queue_capacity_total` | same |
| Error rate | 5xx / all requests > 1 % | API logs (request id in the error body) |
| Latency | p95 of `steward_http_request_seconds` > 0.3 s (reads) | see docs/PERFORMANCE.md |
| DB pool saturation | `steward_db_pool_checked_out` ≥ size + overflow − 2 | raise pool within Postgres limits or scale |
| Model failures | non-success `steward_model_calls_15m` outcomes / all > 20 % | provider status, key, quota, rate limits |
| Brute force | `steward_audit_events_15m{action="auth.login_failed"} > 50` | rate limits already apply; review source IPs in Caddy logs |
| Probing | `authz.denied` + `authz.role_denied` > 20 in 15 min | someone is enumerating IDs; review audit log |
| Approval tampering | `approval.binding_failed > 0` | investigate: an approval was replayed or its parameters changed |
| Second-step failures | `steward_audit_events_15m{action="auth.mfa_failed"} > 10` | someone has users' passwords; affected users are e-mailed automatically after 5 failures |
| Malware found | `file.quarantined > 0` | a user uploaded an infected file; it is quarantined; check who and why |
| Scanner down | `/readyz` reports `malware_scanner: unavailable` (uploads are refused meanwhile) | `docker compose ps clamav freshclam`; logs |
| API container down | Caddy logs "no upstreams available" or only one API healthy for > 5 min | `docker compose ps api api-2`; the other keeps serving |
| Deletion refused | `file.deletion_refused > 0` | expected when someone lacks rights; investigate repeats |
| Backup | `backup.log` has no "done" line in 26 h, or weekly `restore.sh verify` ≠ VERIFIED | fix before anything else |
| Disk | root or Docker volume > 80 % | prune images, move backups off-site (never delete volumes) |
| Certificate | expiry < 14 days | Caddy logs; DNS/ports |

Minimal setup without a monitoring stack (cron on the VPS, mail via your SMTP relay or any
webhook), as the `steward` user:

```bash
*/5 * * * * curl -fsS -o /dev/null https://agent.example.com/login || echo "steward site down" | mail -s ALERT ops@example.com
*/5 * * * * cd /opt/live-agent && docker compose ps --format '{{.Service}} {{.Status}}' | grep -vE 'healthy|caddy' && echo "unhealthy service" | mail -s ALERT ops@example.com
```

For a fuller setup, run Prometheus + Alertmanager (or Grafana Agent) **on the same server**,
attached to the `backend` network, scraping `api:8000/metrics` with the bearer token.

## 5. Audit log access

- Workspace members see their own workspace's activity feed (summaries built from audit events) at
  `GET /activity`; no tenant can read another tenant's events (cross-tenant test in
  `test_authz_matrix.py`). There is no admin-only raw audit viewer in the app; raw rows are read
  in the database with `steward_readonly`.
- `steward_readonly` can SELECT for support/reporting; it cannot write or read secrets.
- Audit rows are append-only for the application roles (no UPDATE/DELETE grants; DB trigger guards
  from migration 0002).

## 6. Incident basics

1. Preserve evidence: `docker compose logs --no-color > incident-<date>.log` (keep it on the server
   or an encrypted location; it may contain e-mail addresses).
2. Contain: `MODEL_PROVIDER=none` and `docker compose up -d` stops all external model traffic;
   `docker compose stop tools` stops all web reads; `docker compose stop orchestrator` stops planning.
3. Rotate what may be exposed (§5 of docs/DEPLOYMENT.md), restore from a verified backup if data
   integrity is in doubt.
