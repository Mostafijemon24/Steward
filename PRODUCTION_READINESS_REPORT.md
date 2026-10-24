# Production readiness report (Phases 4 and 5)

Date: 2026-10-24 · Branch: `phase-5-features-and-scaling` · Version 0.5.0

## Decision

**Still NO-GO for public production. GO for a private staging deployment** on a Hostinger VPS by the
operator (`docs/DEPLOYMENT.md`), with no real customer data until the blockers in §11 are closed.

Phase 5 closed both SLO failures from Phase 4 and three "not built" items. The remaining blockers are
all things that need a real server or real third-party accounts, which this environment doesn't have:
real domain/TLS, SMTP delivery, host firewall, a real model provider, off-site backups, real ClamAV
signature updates and a real WordPress site. This report does not claim the system is secure; it
lists what was tested and what was not.

## Phase 5 summary (features and scaling)

| Item | Result | Evidence |
|---|---|---|
| Orchestrator replicas | Default 2 (`ORCHESTRATOR_REPLICAS`). Plan wait scales ~linearly: 3 s model, 120-task burst → p95 168 s / 82 s / 39 s with 1 / 2 / 4 | `docs/PERFORMANCE.md` §3a, `perf/results/orchestrators-*.json` |
| Phase 4 queue "failure" (P4-07) | **Corrected:** a measurement artifact of the load tool's polling. Server-side plan wait p95 = 1.7 s (1 orchestrator, instant model). Tool fixed. | §3a |
| Two API containers | `api` + `api-2` behind Caddy (health checks, retries), one uvicorn process each | rehearsal |
| Zero-downtime restart (P4-08) | **Fixed:** `deploy/rolling-restart.sh` under ~125 req/s → 12,285 requests, **0 errors**, longest gap 0.09 s | `docs/PERFORMANCE.md` §3b |
| Uneven uvicorn load (P4-09) | Addressed: single-process containers, round robin | — |
| Malware scanning | ClamAV clamd (INSTREAM), fail closed, quarantine; isolated network, read-only, no internet; freshclam separate | 8 tests + real clamd in rehearsal (clean ✓, EICAR quarantined ✓, 20 MB ✓) |
| MFA | TOTP + recovery codes, encrypted seeds, replay protection, rate limits, re-auth, notifications | 18 tests + browser journey 6/6 + rehearsal through Caddy |
| WordPress write | Draft posts only, Application Password sealed to the tools worker's public key, approval-bound content and destination, allowlist HTML | 42 tests (fake WordPress); not tested against a real WordPress site |

### Phase 5 findings and fixes

Independent security review (fresh reviewer, findings first) plus the production rehearsal:

| ID | Sev. | Finding | Status |
|---|---|---|---|
| P5-R1 | Medium | Draft content filter bypassable with HTML entities (`jav&#x61;script:`, `&colon;`, `data:`) | **Fixed**: parser-based allowlist (entity-aware, like a browser); 18 bypass payloads + 7 safe samples tested |
| P5-R2 | Medium | clamd on internet + backend networks, reachable from every container, unhardened | **Fixed**: API-only `scan` network, no internet, read-only FS, SETUID/SETGID only; freshclam separate (verified in rehearsal) |
| P5-R3 | Low | Non-ASCII digits in a code caused a 500 | **Fixed** + test |
| P5-R4 | Low | Password reset left pending sign-in challenges valid | **Fixed** (reset and MFA-off cancel challenges) + tests |
| P5-R5 | Low | Approval bound to planned destination; tool used current site URL | **Fixed**: runner re-checks destinations at execution + test |
| P5-R6 | Low | One key for MFA and WordPress credentials, held by the API | **Fixed**: X25519 sealed box; API has public key only, tools worker the private key; MFA key separate; dev keys refused in production + tests |
| P5-R7 | Low | ClamAV outage made both API containers unhealthy (also seen in the rehearsal) | **Fixed**: `/healthz` = core checks only; `/readyz` reports the scanner; Caddy uses `/healthz` + test |
| P5-R8 | Low | clamd could answer "OK" when limits are exceeded | **Fixed**: `deploy/clamav/clamd.conf` (AlertExceedsMax, AlertEncrypted, StreamMaxLength 32M) + test |
| P5-R9 | Low | Repeated failed second steps unnoticed | **Fixed**: owner e-mailed once after 5 failures in 15 min + test |
| P5-B1 | High (deploy) | `migrate` refused to start after the keys were split (treated as the API) | **Fixed**: `PROCESS_ROLE=migrate` + test; found in rehearsal |
| P5-B2 | High (deploy) | Hardened clamd crash-looped after its first restart (image start script chowns the volume) | **Fixed**: run clamd/freshclam directly; verified restart in rehearsal |
| — | Info | Files uploaded before scanning was enabled are not rescanned; WordPress draft tool not gated by the "external" switch (has its own `wp_write` switch + approval) | Documented |

### Phase 5 checks run (2026-10-24, real results)

| Check | Result |
|---|---|
| Backend `uv run pytest -o addopts="" -q` | **496 passed, 1 skipped** (same root-only sandbox skip as before) |
| ruff / ruff format / mypy (65 files) / bandit -ll / pip-audit / lockfile sync | all clean |
| Migration 0007 up/down/up + `alembic check` | passed |
| Web lint / typecheck / vitest | passed; **53 tests** |
| Web production build + bundle secret scan + `npm audit --omit=dev` | passed; 37 files clean; 0 vulnerabilities |
| Browser e2e smoke (real backend) | **25/25** |
| Browser e2e MFA journey (`e2e/mfa.mjs`) | **6/6** |
| Production rehearsal (Docker, `APP_ENV=production`, Caddy TLS, 2 APIs, 2 orchestrators, real clamd) | all services healthy; MFA, scanning, sealed WordPress credentials verified through Caddy; internal paths 404 (10 variants); only 80/443 public; backup at schema 0007 → restore verify **VERIFIED** |
| Rolling restart under load | 0 errors / 12,285 requests |
| Not verifiable here | ClamAV signature download (blocked by sandbox network policy; a local test signature was used), real WordPress, real model, SMTP, real domain/TLS |

---

# Phase 4 report (kept for reference; superseded items are marked)

## 1. Architecture and services

One VPS, Docker Compose, 9 services (diagram and table in `docs/DEPLOYMENT.md` §1):
Caddy (TLS, the only public entry point: 80/443) → Next.js web (proxies `/api/*`) → FastAPI API →
PostgreSQL 16, plus four workers (orchestrator, tools, deletion, scheduler) using a durable Postgres
queue (`SKIP LOCKED`) and a one-shot `migrate` service. Three Docker networks: `backend` (internal,
no internet), `edge` (Caddy ↔ web), `egress` (outbound only, for orchestrator and tools). Five
least-privilege database roles. App containers are non-root (uid 999) with read-only root
filesystems, no capabilities, and `no-new-privileges`. Caddy keeps only `NET_BIND_SERVICE`.

## 2. Integrations: working vs. mock vs. not built

| Integration | State |
|---|---|
| AI model, OpenAI-compatible (OpenAI, gateways, Ollama/vLLM) | Built. Tested against a local stub server and with the deterministic `mock` provider. **No real provider was called in this phase.** |
| `MODEL_PROVIDER=none` | Working (verified in the rehearsal: tasks are saved and wait, nothing is sent anywhere) |
| WordPress | Read-only via the public REST API, allowlisted domain, worker-verified. Write is **not built**. |
| SMTP e-mail | Built (STARTTLS). **Not verified against a real mail server**: the rehearsal used `smtp.invalid`, with e-mail verification done in SQL. |
| Web reads (tools worker) | Built, allowlist plus egress checks. Covered by tests; Phase 3 e2e against a local test site. |
| Code sandbox | Linux namespaces and resource limits. Not VM-grade. |
| Malware scanning, Notion, cloud storage, e-mail sending as a tool, browser automation, MFA, RLS, hash-chained audit | **Not built** (listed honestly in the UI and in `SECURITY_AND_PRIVACY.md`) |
| `mock` provider, demo mode | Development only; refused in production (tested) |

## 3. Findings this phase (by severity)

All findings come from tests or the production rehearsal; nothing comes from speculation.
"Fixed" means a regression test or a re-run rehearsal shows the fix.

| ID | Sev. | Finding | Evidence | Status |
|---|---|---|---|---|
| P4-01 | **High** | Restoring a backup left all tables owned by the superuser. The migration role then could not read `alembic_version`, `migrate` failed, and the app could not start after a disaster recovery. The scratch "verify" restore had not caught it because it only compared row counts. | Full restore drill: `permission denied for table alembic_version` | **Fixed**: `restore.sh` restores as `steward_owner` (`--role`) into a fresh database and swaps it in by rename, keeping the old database for rollback. `verify` now checks table ownership and key grants. Drill re-run: passed. |
| P4-02 | **High** | All four workers crash-looped in production mode. The API-only production guards (SMTP, proxy headers, https origin) also ran in worker processes, which deliberately have mail disabled. With workers down, no task can ever be planned, executed or deleted. | Rehearsal: `Restarting (1)`, "Unsafe production configuration" | **Fixed**: `PROCESS_ROLE=worker` skips only the HTTP-only checks (secrets, egress, mock-model checks still apply). The default role is the strict `api`, and the API refuses to start with the worker role. Tests: `test_worker_role_skips_http_only_guards_but_keeps_the_rest`, `test_api_refuses_to_start_with_the_worker_role`. |
| P4-03 | **High** (functional) | Uploads over 10 MB failed with 500 on the production path. The Next.js `/api` rewrite truncates request bodies at 10 MB by default; the API limit is 25 MB. Unit tests could not see this because they call the API directly. | Rehearsal: 12 MB and 20 MB uploads → 500, `socket hang up` | **Fixed**: `experimental.proxyClientMaxBodySize: "30mb"` (matches the Caddy cap). Re-tested: 10 KB–20 MB uploads round-trip with matching sha256. |
| P4-04 | Medium | DB connection pool exhaustion under load: requests waited 30 s for a connection (p95 30 s, 4.9 % timeouts at 100 VUs) | Step load test | **Fixed**: configurable pool (10+10), 5 s pool timeout returns a fast `503 service_busy` with `Retry-After`, plus a per-process concurrency limit. p95 30,328 ms → 521 ms. Test: `test_database_pool_exhaustion_returns_503_quickly`. |
| P4-05 | Medium | Prerendered HTML pages were served with `Cache-Control: s-maxage=31536000`, allowing a shared cache or CDN to keep pages for a year (stale after deploys; risky if a page ever becomes user-specific) | Rehearsal edge check | **Fixed**: Caddy forces `no-store` on everything except content-hashed `/_next/static/*`. Re-checked. API responses were already `no-store` (`test_authenticated_api_responses_are_never_cacheable`). |
| P4-06 | Medium | Anonymous multipart uploads were parsed and spooled (up to 30 MB) before authentication ran | Rehearsal: anonymous upload got a parser error, not 401 | **Fixed**: the middleware returns 401 before reading the body when no session cookie is present. Test: `test_anonymous_multipart_is_refused_before_the_body_is_read`. |
| P4-07 | — | ~~Queue wait SLO fails under a burst (p95 37 s)~~ | Queue saturation test | **Withdrawn in Phase 5**: measurement artifact of the load tool; server-side p95 1.7 s |
| P4-08 | Medium | A single API instance means restarts cause a short outage (2.4 s, 3 % refused) | Restart-under-load test | **Fixed in Phase 5** (two API containers + rolling restart: 0 errors) |
| P4-09 | Low | Uneven load across uvicorn processes | perf CPU samples | **Addressed in Phase 5** (single-process containers) |
| P4-10 | Low | Caddy advertised HTTP/3 although UDP 443 is not published | Rehearsal headers | **Fixed**: `protocols h1 h2` |
| P4-11 | Low | Caddy ran as root with default capabilities | `docker inspect` | **Fixed**: `cap_drop ALL` + `NET_BIND_SERVICE`, read-only FS, `no-new-privileges`. Re-tested. |
| P4-12 | Low | Request metrics are per uvicorn process, so a scrape sees one process | Code review | **Open**: documented in `docs/OPERATIONS.md`; DB-derived metrics are global. |
| P4-13 | Info | bandit B108 (`HOME=/tmp`) in the sandbox | bandit | Reviewed false positive (private tmpfs inside the namespace); annotated |
| P4-14 | Info | deptry: 10 "unused/missing" items | deptry | Reviewed false positives (module ≠ package name, or loaded indirectly). Nothing removed. |

No Critical findings. Earlier phases' findings (SR-1…SR-13, Phase 3) remain fixed; their regression
tests are part of the 426 passing tests.

## 4. Security and privacy review: requirement status

Legend: **Verified** = automated test or rehearsal evidence in this repo · **Partial** = verified
in part, or only in a test environment · **Not verified** = needs the real environment · **Failed**.

| Critical requirement | Status | Evidence |
|---|---|---|
| Authentication on every route | **Verified** | `test_anonymous_callers_are_refused_everywhere` enumerates every route from OpenAPI (401) |
| Authorization / roles (viewer, member, admin) | **Verified** | `test_viewer_cannot_use_write_routes`, `test_member_cannot_use_admin_routes` (all write routes, generated) |
| Tenant isolation / IDOR / BOLA | **Verified** | `test_cross_tenant_access_returns_not_found_and_changes_nothing`: every ID-taking route, 404, data checksums unchanged |
| Password hashing, sessions, cookies, CSRF | **Verified** | argon2; `__Host-` cookies, `Secure`, `SameSite`; Origin + double-submit CSRF (Phase 2/3 tests); `__Host-` cookies confirmed through Caddy |
| Rate limits | **Verified** (tests) | auth, writes, uploads, task creation, ID probes; production refuses `RATE_LIMIT_ENABLED=false` |
| HTTPS, HSTS, security headers | **Partial** | Verified in the rehearsal with Caddy's local CA (TLS 1.3, HSTS, CSP, 308 redirect). A real Let's Encrypt certificate was **not** tested. |
| Only required ports exposed | **Partial** | Rehearsal: only 80/443 public, web on 127.0.0.1, db/api/workers unpublished. Host firewall (ufw/Hostinger) **not verified** (no VPS). |
| Internal endpoints hidden | **Verified** | `/metrics /readyz /livez /healthz /api/v1/docs /openapi.json` return 404 via Caddy; `/metrics` 404 without a valid token |
| No public caching of private data | **Verified** | API `no-store` test; Caddy `no-store` on pages (P4-05); only hashed static assets cached |
| No secrets in bundles / responses / logs / repo | **Verified** | bundle scan (37 files); model-key leak test over all GET responses; redaction tests; detect-secrets on tracked files (baseline reviewed: placeholders and test fixtures only); git history scanned in P4-1 |
| Strict private mode / egress allowlist | **Verified** (app level) | egress and policy tests; `MODEL_PROVIDER=none` sends nothing. A network-level egress firewall is **not** in place. |
| Worker isolation, least-privilege DB roles | **Verified** | role grants tested; restore check confirms grants; workers use their own roles; deletion service is the only role that can delete |
| Prompt injection / argument validation | **Verified** (tests) | Phase 3 injection and policy re-check tests; server-side argument validation |
| Deletion approvals, bound and expiring approvals | **Verified** | approval binding/expiry/replay tests; e2e smoke: deletion request → approval → deletion service → restorable |
| Private uploads | **Verified** | content sniffing, `0600` files, no web root; anonymous upload refused early (P4-06) |
| Audit access | **Verified** | tenant-scoped activity feed; append-only trigger; `approval.binding_failed` etc. exported as metrics |
| No stack-trace leaks | **Verified** | uniform error envelope with request id; checked through Caddy (404/401/422/413 bodies) |
| Backup and restore | **Verified** (rehearsal) | §7. Off-site copy and cron on the real server: **not verified** |
| Production config guards | **Verified** | API and worker guard tests; rehearsal started only after fixes |
| Real SMTP delivery | **Not verified** | needs real mailbox |
| Real model provider (latency, cost, failures) | **Not verified** | only stub/mock; gateway retry/429 logic unit-tested |
| Deployment on a real VPS | **Not verified** | no VPS access in this environment |
| Queue wait SLO under burst | **Verified** (Phase 5, mock model; real model not measured) | §3a of PERFORMANCE.md |
| Zero-downtime restart | **Verified** (Phase 5 rehearsal: 0 errors) | PERFORMANCE.md §3b |
| Malware scanning | **Partially verified** (real clamd in rehearsal with a test signature; signature updates not verifiable here) | Phase 5 |
| MFA | **Verified** (tests, browser e2e, rehearsal) | Phase 5 |
| RLS, VM-grade sandbox, network egress firewall, workspace-wide MFA requirement | **Not built** | `SECURITY_AND_PRIVACY.md` |

OWASP mapping (summary): ASVS V2/V3 session and auth → verified rows above; V4 access control →
authz matrix; V5 validation → schema validation and upload sniffing; V7 errors/logging → no stack
traces, no secrets; V8 data protection → `no-store`, private storage; V12 files → upload controls;
V14 config → production guards and headers. OWASP API Top 10: API1 BOLA and API5 function-level
authz → matrix tests; API4 resource consumption → limits, quotas, pool/concurrency shedding;
API8 misconfiguration → guards, hidden internals. OWASP LLM Top 10: LLM01 prompt injection and
LLM06 excessive agency → policy engine, approvals, tool allowlist; LLM02 sensitive disclosure →
redaction, key never in prompts; LLM10 unbounded consumption → token/call quotas. A formal
line-by-line ASVS assessment was **not** performed.

## 5. Tests and checks run (exact commands, real results, 2026-10-24)

| Check | Command | Result |
|---|---|---|
| Backend tests (separate `steward_test` DB) | `cd api && uv run pytest -o addopts="" -q` | **426 passed, 1 skipped** (skip: RLIMIT_NPROC isn't enforced for root in this sandbox; workers run non-root in production) |
| Lint / format | `uv run ruff check app tests && uv run ruff format --check app tests` | Passed |
| Types | `uv run mypy app` | Passed (60 files) |
| Migrations | `alembic upgrade head && downgrade base && upgrade head && alembic check` (isolated DB) | Passed, no model/schema drift |
| Static analysis | `uvx bandit -r app -q -ll` | Passed (1 reviewed false positive annotated) |
| Python advisories | `uvx pip-audit -r requirements.lock` | No known vulnerabilities |
| Unused deps | `uvx deptry .` | 10 items, all false positives (P4-14) |
| Semgrep | `uvx semgrep --config auto` | **Blocked**: registry unreachable through the sandbox proxy (403) |
| Web lint / types / unit | `npm run lint && npm run typecheck && npm test` | Passed; 39 tests |
| Web build | `NEXT_PUBLIC_DEMO_MODE=false npm run build` | Passed |
| Bundle secret scan | `npm run check:bundle` | 37 files, nothing secret-like |
| Web advisories | `npm audit` | 0 vulnerabilities |
| Secret scan | `detect-secrets scan` (tracked files) vs `.secrets.baseline` | Only reviewed placeholders and fixtures |
| Browser e2e (real backend) | `WEB_URL=http://localhost:3100 node e2e/smoke.mjs` | **25/25 PASS**, 0 console errors |
| Agent e2e flow | `e2e/agent-flow.mjs` | Not re-run this phase (passed in Phase 3; engine code unchanged except the worker config role) |
| CI workflow | `actionlint`; GitHub Actions run 40116735033 | Passed: all 4 jobs green (API incl. tests on a fresh Postgres, migrations and audits; web; secret scan; both images built). The first run (40116494311) failed in the secret-scan job because of my workflow scanning `.git/`; fixed. |
| Compose | `docker compose --env-file .env.example config -q` | Passed |

## 6. Performance (summary; full detail in `docs/PERFORMANCE.md`)

Environment: one 4 vCPU / 16 GB machine shared by API, Postgres, workers and the load generator;
mock model; rate limits off for measurement. 1 virtual user is a tight loop with 0.2–1 s think
time, **not** a person.

| Scenario | VUs | req/s | read p95 | read p99 | errors | SLO |
|---|---|---|---|---|---|---|
| Baseline | 10 | 16 | 25 ms | 147 ms | 0 % | Pass |
| Step | 25 | 39 | 39 ms | 582 ms | 0 % | Pass |
| Step | 50 | 74 | 143 ms | 1,089 ms | 0 % | Fail (p99) |
| Step | 100 | 113 | 521 ms | 2,411 ms | 0.76 % (fast 503) | Fail |
| Spike 5→150→5 | 150 | 83 | 6.9 s | 11.4 s | 2.5 % (fast 503) | Fail during spike, recovered immediately |
| Soak 10 min | 20 | 32 | 33 ms | 53 ms | 0 % of 19,365 | Pass; memory and DB connections flat |
| Queue saturation | 300 tasks | — | — | — | 0 rejected | plan wait p95 37 s: **Fail** |
| Worker SIGKILL | 60 tasks | — | — | — | 0 lost | Pass |
| API restart under load | 10 | — | — | — | 3 % refused | 2.4 s outage |

Measured comfortable range on this hardware: about 25 VUs within all latency SLOs. **No user
capacity is promised.** The real model's latency and cost were not measured. Nothing here supports
claims of thousands or millions of concurrent users. When to scale beyond one VPS:
`docs/PERFORMANCE.md` §8.

Safeguards in place: rate limits, per-workspace quotas, bounded worker concurrency, queue
backpressure (`503 queue_full`), pagination on every list, DB statement timeout 15 s, pool
timeout 5 s, uvicorn concurrency/keep-alive/graceful limits, Caddy header/body/response timeouts,
30 MB body cap, indexes (migration 0006), `/livez` `/readyz` `/metrics`.

## 7. Backup and restore verification

Performed on the production-mode rehearsal stack (`APP_ENV=production`, Caddy TLS):

| Test | Result |
|---|---|
| `deploy/backup.sh` (DB + 8 stored files + manifest) | Passed, 1.5 s |
| `deploy/restore.sh verify` (scratch DB) | **VERIFIED**: row counts, migration head `0006`, 8/8 files, table ownership 0 wrong, grants as expected |
| Tamper test (1 byte changed in `db.dump`) | Refused: "db.dump checksum mismatch" |
| age-encrypted backup | Only `.age` files stored; verify without key refused; with key **VERIFIED** |
| Full destructive restore drill (data added after backup, then restore) | First run **failed** (P4-01). After the fix: post-backup data gone (users 3→2, tasks 9→6, files 13→8), migrations OK, all services healthy, 8/8 stored files match their DB sha256, pre-backup user logged in through Caddy and downloaded files with matching sizes |
| Measured RTO (small data set) | 47 s restore + restart |
| RPO | 24 h with the documented daily cron (configurable) |
| Rollback commands printed by `restore.sh` | **Not exercised** |

Not verified: cron on a real server, off-site copy, restore with production-sized data.

## 8. Deployment package

| Item | Where |
|---|---|
| Step-by-step Hostinger guide: server hardening, ufw, Docker, DNS, secret generation, first start, health, logs, backups, releases/rollback, upgrades, troubleshooting, go-live checklist | `docs/DEPLOYMENT.md` |
| Monitoring, alerts, telemetry policy (nothing leaves the server by default) | `docs/OPERATIONS.md` |
| Compose (networks, hardening, health checks, log rotation, restart policies) | `docker-compose.yml` |
| Reverse proxy and TLS | `deploy/Caddyfile` |
| Env reference with placeholders only | `.env.example` |
| Backup / restore | `deploy/backup.sh`, `deploy/restore.sh` (`deploy/db/backup.sh` now forwards to the new script) |
| CI (locked installs, lint, types, tests, migration check, audits, secret scan, image build, release artifact on tags; **no deploy**) | `.github/workflows/ci.yml` |
| Load testing | `perf/` |

## 9. What changed in this phase (code)

Observability module (`/livez`, `/readyz`, `/healthz`, token-protected `/metrics`); configurable
DB pool with fast 503 shedding; uvicorn concurrency limits; migration 0006 (indexes); authz/tenant
matrix tests; worker process role; early 401 for anonymous uploads; Next.js body limit; Caddy
hardening and `no-store` for pages; backup/restore tooling; CI workflow; docs.

## 10. Risks that remain

- Not deployed to a real VPS: TLS issuance, SMTP, firewall and DNS are unverified.
- No network-level egress firewall. Egress control is enforced in the application only.
- Code sandbox is namespace-based, not VM-grade.
- Malware scanning is optional (needs ~1.5 GB RAM); without it uploads are marked `unscanned`.
- MFA is optional per user (no workspace-wide requirement). Login and second-step limits can be abused to lock a known account out temporarily (accepted risk; owner is notified).
- Single server: hardware, disk or provider failure means downtime until restore. API restarts are zero-downtime with the rolling script; restarting the web container or the database is not.
- Audit log can be altered by the schema owner (trigger can be disabled); no hash chain.
- `'unsafe-inline'` scripts in the web CSP.
- Model provider behaviour (latency, cost, outages, data handling) is untested with a real provider.

## 11. Manual actions required before public production (blockers)

1. Deploy to the VPS following `docs/DEPLOYMENT.md` and complete the go-live checklist (§15 there).
2. Verify real TLS (Let's Encrypt), the firewall (`ss -tlnp`, ufw, Hostinger panel) and DNS.
3. Verify SMTP: verification and password-reset e-mails arrive.
4. Configure and test the chosen model provider (or keep `none`); review its data-processing terms.
5. Schedule backups, copy them off-site, and run `restore.sh verify` successfully on the server.
6. Keep CI green; protect the release branch so merges require it.
7. Once a real model is configured, measure its latency and set `ORCHESTRATOR_REPLICAS` with the rule in PERFORMANCE.md §3a.
8. Wire at least the "site down", "worker missing", "backup" and "not ready" alerts to a person.
9. If you enable malware scanning: confirm freshclam downloaded signatures and the EICAR test file is quarantined (DEPLOYMENT.md §12).
10. If you enable WordPress drafts: test once against your real site with an Application Password of a non-admin user.

Credentials were never requested or printed during this work. Rehearsal secrets were randomly
generated in a scratch file outside the repository and are discarded with the rehearsal.
