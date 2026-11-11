# Architecture — Steward AI Agent Workspace

Status: accepted · Date: 2026-10-24 · Scope: Phase 2 (full-stack foundation, D1–D17) and Phase 3 (agent engine, D18–D31). Not deployed to production.

## 1. Context

Phase 1 produced an approved, single-file design prototype (`project/Steward AI Agent Workspace.dc.html`, see `project/docs/`). Phase 2 turns it into a real application that a non-technical user reaches through a browser, deployable to a single Hostinger VPS, without making the product one giant process.

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Two services**: `web/` (Next.js 16, TypeScript, App Router) and `api/` (Python 3.13, FastAPI). PostgreSQL 16 as the only stateful dependency. | The approved README named Next.js. The spec prefers FastAPI when no backend exists. Python is also the natural home for the Phase 3 agent/worker code, so the API, planner and workers share one language and one data layer. |
| D2 | **Same-origin deployment.** The browser only talks to `web`. Next.js rewrites `/api/*` to the API service on the private network. | First-party cookies, no CORS, `SameSite=Lax` works, the API is never exposed directly. |
| D3 | **SQLAlchemy 2 + Alembic** for the data layer and versioned migrations. Migrations are additive; a migration test runs upgrade → downgrade → upgrade on a throwaway `_test` database only. | Mature, explicit, typed. |
| D4 | **Two database roles.** `steward_owner` owns the schema and runs migrations. `steward_app` is what the API uses at runtime: `SELECT/INSERT/UPDATE` only, **no `DELETE`** (except the rate-limit table), no DDL, `INSERT/SELECT` only on `audit_events`. | Least privilege. The general application/agent role physically cannot delete tasks, files, approvals or audit records (spec: deletion safety). |
| D5 | **Server-side sessions** in Postgres. Opaque 256-bit token in an `HttpOnly`, `SameSite=Lax`, `Secure` (production) cookie. Only the SHA-256 of the token is stored. Idle timeout 12 h, absolute lifetime 7 days, rotated at login, all revoked on password reset. | Revocable, simple, nothing secret in `localStorage`. |
| D6 | **CSRF**: double-submit token (`stw_csrf` cookie + `X-CSRF-Token` header) on every unsafe method, including login/register, plus an `Origin` check against `APP_ORIGIN`. | Cookie auth needs CSRF defence; the Origin check is defence in depth. |
| D7 | **Password hashing: Argon2id** (`argon2-cffi`, library defaults, automatic rehash on login when parameters change). | Current OWASP recommendation. |
| D8 | **Rate limiting** in Postgres (fixed window, `INSERT … ON CONFLICT` counter). Client IP = peer address, or `X-Real-IP` overwritten by Caddy when `TRUST_PROXY_HEADERS=true`; `X-Forwarded-For` is never trusted. | Works across several API worker processes without adding Redis in Phase 2. Phase 3 can swap the `RateLimiter` implementation. |
| D9 | **Tenancy**: every user gets a private workspace at registration; data is owned by `workspace_id`; access goes through `workspace_memberships` (role `owner` now, `admin/member/viewer` reserved). The active workspace is resolved **server-side from the session**, never from request input. Every repository query is scoped by `workspace_id`; a foreign ID returns `404 not_found` and writes an `authz.denied` audit event. Composite foreign keys `(workspace_id, id)` stop child rows from pointing at another tenant's parent. | Correct application authorization first. PostgreSQL RLS is **not** enabled in Phase 2 (see §6); we do not claim it. |
| D10 | **Explicit task state machine** in `api/app/domain/task_states.py`, enforced in one function, tested exhaustively. Without a planner, a new task is persisted as `queued` with `waiting_for = "planning_service"`. Nothing ever shows a fake completed result. | Spec requires honesty and an explicit state machine. |
| D11 | **Ports-and-adapters interfaces** in `api/app/ports/`: `TaskPlanner`, `ApprovalPolicy`, `ModelProvider`, `JobQueue`, `Worker`, `ToolRegistry`, `AuditLogger`, `FileStorage`, `FileScanner`, `Mailer`. Phase 2 ships safe adapters: `UnavailablePlanner`, `NullJobQueue` (records intent, never runs), `NotConfiguredScanner`, `RequireApprovalPolicy`, `LocalPrivateFileStorage`, `FileOutboxMailer` (dev) / `SmtpMailer`. | Phase 3 replaces adapters without touching routes or UI. |
| D12 | **Private file storage**: uploads are streamed to `STORAGE_DIR` (outside any web root, not served statically) under random names (`<workspace_uuid>/<uuid>`); type detected from magic bytes against an allowlist; size limit enforced while streaming; path traversal impossible because client names are never used as paths. Files start `unscanned` ("scanner not configured") and classification `unknown` (sharing blocked). Downloads go through an authenticated, tenant-checked endpoint as `attachment` with `nosniff`. | Spec: file security. |
| D13 | **Deletion is a request, not an action.** `deletion_requests` + an `approval_requests` row of category `record_deletion`. *Phase 2:* approving recorded the decision only. *Phase 3:* see D29 — a separate deletion service executes approved requests after re-validation. | Spec: deletion safety. |
| D14 | **Audit log** `audit_events`: append-only (app role has no UPDATE/DELETE; a trigger also rejects UPDATE/DELETE for every role), scoped by workspace, metadata scrubbed of secret-looking keys. The user-facing Activity feed reads from it. | Spec: audit logging. |
| D15 | **API**: REST under `/api/v1`, Pydantic schemas, `{"error": {"code", "message", "request_id", "details"}}` envelope, `X-Request-ID` on every response, page/page_size pagination with `total`. OpenAPI at `/api/v1/openapi.json` (disabled in production). | Spec: API contract. |
| D16 | **Frontend API adapter**: `web/lib/api` defines one `AgentApi` interface with `HttpAgentApi` (real) and `MockAgentApi` (the Phase 1 demo data). The mock is chosen only when `NEXT_PUBLIC_DEMO_MODE=true`; the production build refuses to enable it unless explicitly set, and the UI shows the DEMO strip whenever it is on. HTTP errors are surfaced, never converted into mock success. | Spec: isolated demo mode, no silent fallbacks. |
| D17 | **Docker Compose** for development and VPS: `db` (no published port), `api`, `web`, optional `caddy` for TLS. Only `web`/`caddy` publish ports. | Reproducible, Hostinger-friendly. |

### Phase 3 — agent engine

| # | Decision | Why |
|---|---|---|
| D18 | **Process split.** The API only records decisions and queues jobs. Separate worker processes (same image): `orchestrator` (planning, step sequencing), `tools` (tool execution + sandbox + integration checks), `deletion` (approved deletions, restore, purge), `scheduler` (automations, purge scheduling, stale-job recovery). `python -m app.workers <kind>`. | Model output and tool execution never run in the web/API process. A crashed or compromised tool worker has no API credentials. |
| D19 | **Durable queue in PostgreSQL** (`jobs` table): `FOR UPDATE SKIP LOCKED` claims, priority + `run_after`, per-workspace running cap (fair share), pending caps (backpressure → `503 queue_full`), heartbeats with a lease, stale-job recovery, exponential backoff with full jitter, bounded attempts, dead-letter (`dead`) and `failed` (non-retryable), idempotent enqueue via `(workspace_id, dedupe_key)`, cancellation of pending jobs per task. | No new infrastructure (Redis/RabbitMQ) on a single VPS; transactional with the data it changes. Throughput is limited by PostgreSQL; documented. |
| D20 | **Database roles per process.** `steward_worker` (orchestrator/tools/scheduler): no `DELETE`, no access to sessions, auth tokens or password hashes, column-limited updates (cannot change workspace policy, file classification, approval decisions). `steward_deleter`: reads what it must re-validate; the only role that can set `files.deleted_at`; no `DELETE`. `steward_readonly`: `SELECT` without secrets. `steward_app` lost `UPDATE` on `files.deleted_at/purge_after`. Grants live in migration 0004; roles are created by `deploy/db/init-roles.sh`. | Least privilege enforced by PostgreSQL, tested in `tests/test_db_roles.py`. No role can run DDL or TRUNCATE; no general-purpose SQL tool exists. |
| D21 | **Provider-neutral model interface** (`app/engine/providers.py`): `ModelProvider.generate()`, `ProviderInfo` (label, model, `local`/`external`, plain-language data disclosure). Adapters: `NoProvider` (default), `MockProvider` (deterministic, in-process, refused in production), `OpenAICompatibleProvider` (OpenAI or self-hosted Ollama/vLLM via `MODEL_BASE_URL`), `OpenRouterProvider` (`MODEL_PROVIDER=openrouter`; default `MODEL_FREE_ONLY=true` uses `openrouter/free` and may auto-discover `:free` catalog fallbacks — still one provider). Every call goes through `ModelGateway`: configured check, privacy authorisation, redaction, input limit, monthly token/call quotas, bounded retries with backoff, one `model_calls` row per attempt (usage only, never prompt text). **No fallback** to another provider. | Swappable providers without leaking data by accident; usage visible; no silent switching. |
| D22 | **Strict privacy on the server.** Data reaches an *external* provider only if the policy engine allows it and, where required, a one-time `external_transfer` approval bound to the exact data (task-text hash, attachment ids, provider, model) exists. With no model configured, tasks wait and nothing is sent. Local providers are described as "your infrastructure", not "stays in the app". | A prompt is not a security boundary; the gateway refuses unauthorised transfers. |
| D23 | **Planner with strict validation** (`app/engine/planner.py`). The model proposes only `goal`, `tool`, `args`, `expected_output` per step. Tool must exist; args validated by the tool's Pydantic schema (`extra=forbid`); file ids must be attached to the task; step references only backwards; ≤ `MAX_PLAN_STEPS`. Permissions, classifications, destinations, impact, approval requirement, idempotency, timeout and retries are derived on the server. Extra fields are ignored (and counted). One bounded repair attempt, then a visible failure. Each re-plan creates a new `plan_version`. | Model output is untrusted input. |
| D24 | **Policy engine, default deny** (`app/engine/policy.py`, `POLICY_VERSION`). Baseline that settings cannot weaken: unknown/unbuilt/switched-off tools denied; network only to allowlisted domains; `unknown` data never leaves; `confidential` leaves only with approval; publish/send/production/permission changes and code execution always need approval; prepare mode never applies changes; deletion only via the deletion workflow. Privacy modes: *strict* asks before any external read or transfer; *approval* asks before any transfer; *custom* lets public/internal data go to the configured provider unless the AI rule "ask before sharing" is on. Evaluated at planning **and again right before each step runs**. | One place decides; re-checking closes the gap between approval and execution. |
| D25 | **Approvals bound to the exact operation.** HMAC-SHA256 (`APPROVAL_SIGNING_KEY`) over every displayed field plus `operation`, canonical `args_hash`, sorted `targets`, `step_id`, expiry and `policy_version`. Before execution the worker verifies: same workspace, approved, `pending_execution`, same operation/args/targets/step/policy version, not expired, signature valid, a matching decision row, and the approver is still an owner/admin. It is consumed once (`executed`); retries of the same job may reuse it; anything else marks it `invalidated` and a fresh approval is requested. No blanket or persistent approvals. | Approval of A cannot be used for B, later, by someone else, or under a different policy. |
| D26 | **Tool registry** (`app/engine/tools/`): `files.read`, `files.analyze`, `web.inspect`, `seo.audit`, `wordpress.read`, `report.write`, `ai.summarize`, `code.run_python`, `files.request_deletion`; `wordpress.write`, `email.send`, `browser.automation` are listed as **not available**. No shell, SQL or generic HTTP tool. Tool output is redacted, size-capped and stored as data; web content is parsed into fields and never fed to the planner as instructions. | Small, auditable capability surface. |
| D27 | **Egress control** (`app/engine/egress.py`): allowlist (domain + subdomains), DNS resolved and every address checked (no loopback/private/link-local/metadata/reserved, IPv4-mapped IPv6 handled), connection pinned to the checked IP with TLS SNI/verification on the hostname (no DNS rebinding), redirects only to the same host, response cap, timeouts, environment proxies ignored (optional `EGRESS_PROXY_URL`). Compose adds network separation: API, deletion and scheduler have no internet route; only orchestrator and tools join the `egress` network. | Defence in depth against SSRF and exfiltration. A network firewall is still recommended in production. |
| D28 | **Sandbox** (`app/engine/sandbox.py`) for `code.run_python`, tools worker only: `unshare` user/network/mount/PID namespaces, read-only root, private size-limited `/tmp`, empty tmpfs over the app dir/storage/mail/home/secrets, empty environment, `python -I -S`, rlimits (CPU, memory, file size, open files, no core), wall-clock kill of the process group, output caps. Self-test at startup; if unavailable (e.g. Docker's default seccomp) the tool is unavailable and code steps are blocked — **fails closed**. Always requires approval. | Isolation for small programs; not claimed to stop a determined kernel-level attacker (gVisor/Firecracker recommended for hostile code). |
| D29 | **Deletion workflow** (`app/engine/deletion.py`, `steward_deleter`): preview (file id, name, size, SHA-256) → one-time approval of exactly that content → deletion service re-validates (binding, approver role, file unchanged, same workspace, not already deleted) → soft delete in one transaction (row `deleted_at` + `purge_after`, object moved to trash; storage move undone if the commit fails) → audit → restore by owner/admin during retention → purge of bytes after retention (tombstone row kept). The agent can only *propose* deletions. | Recoverable, approved, re-validated, audited. |
| D30 | **Integration framework**: integrations are marked `operational` only with a real adapter. First real one: **WordPress read-only** via the public REST API — site URL only, no credentials, domain must be allowlisted, verified by the tools worker before `connected`. The AI provider is configured by the administrator through environment variables (never in the browser). Others are listed as not built. | Honest status; safest genuinely testable integration first. |
| D31 | **Observability**: structured worker logs with job/task ids, worker heartbeats (`workers` table), queue depth per workspace, `model_calls` usage, audit events for plan creation, policy blocks, approval binding failures, step execution, deletion/restore/purge — exposed via `/agent/status`, the dashboard and the Activity feed. Audit rows written by workers have no actor (shown as the agent) and carry `job:<id>` as request id. | Operators and users can see what ran, why, and what was blocked. |

## 3. Repository layout

```
api/                     FastAPI service
  app/
    core/                config, db, security (hashing, tokens), errors, request ids, rate limit
    domain/              task state machine, enums, policies
    models/              SQLAlchemy models
    schemas/             Pydantic request/response models
    ports/               interfaces (planner, queue, storage, scanner, mailer, audit, …)
    adapters/            storage, mail, scanner (Phase 2 adapters)
    engine/              Phase 3: policy, planner, providers, egress, redaction, sandbox, queue,
                         orchestrator, runner, deletion, scheduler, integrations, tools/
    workers/             worker processes (python -m app.workers <kind>)
    services/            use cases (auth, tasks, approvals, files, audit, agent glue, …) — tenant-scoped
    routers/             HTTP routes (thin)
  migrations/            Alembic
  tests/                 pytest (isolated *_test database)
web/                     Next.js app
  app/                   routes (public, auth, (app) shell)
  components/            ui kit + shell
  features/              one folder per product area
  lib/api/               AgentApi interface, http + mock adapters, types
deploy/                  compose helpers, db init, Caddyfile
project/, chats/         Phase 1 design bundle (source of truth for visuals)
```

## 4. Request lifecycle

Browser → `web` (`/api/v1/...` rewrite) → FastAPI middleware (request ID → security headers → body-size limit) → dependency `current_context` (session cookie → user → membership → workspace) → CSRF check on unsafe methods → rate limit (where configured) → service (tenant-scoped query, transaction) → audit event → response.

## 5. Dependency versions

Pinned in `api/pyproject.toml` + `api/uv.lock` (exported to `api/requirements.lock` for the Docker image) and `web/package.json` + `web/package-lock.json`. Headline versions are listed in `README.md` (Stack).

## 4b. Task lifecycle (Phase 3)

`POST /tasks` → `queued` + `task.plan` job → orchestrator: provider configured? policy for sending the task to the model (may create an `external_transfer` approval and wait) → `planning` → validated plan version N → `awaiting_approval` → user approves selected steps → `queued` + `task.execute` → orchestrator: per step re-evaluate policy → deny (`blocked`) / approval needed (`needs_input`, step `awaiting_approval`, one-time approval) / allow → `tool.run` job → tools worker: re-check task status, args hash, policy, approval binding (consume) → run with timeout and cancellation → store redacted output/artifact → `task.execute` → … → `completed` / `partially_completed` / `failed` with a report. Cancel stops pending jobs and discards a running step's result; pause finishes the current step and starts nothing new.

## 6. Not built / out of scope (explicitly)

- Public production deployment (not done in this phase).
- Malware scanning (interface + `unscanned` state only).
- PostgreSQL Row-Level Security — isolation is enforced by the application, composite FKs and role grants, and proven by tests; RLS remains future hardening.
- Encrypted per-workspace secret storage and authenticated integrations (WordPress write, email, cloud storage, Notion, full browser automation).
- Hash-chained (tamper-evident) audit log; the schema owner can still disable the append-only trigger.
- Email delivery in development is a local outbox directory, not real email.
