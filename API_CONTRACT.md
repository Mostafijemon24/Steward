# API contract — `/api/v1`

The browser calls these through the web app's own origin (`/api/v1/...` is forwarded to the API service). Types are defined twice and kept in sync by a test: Pydantic in `api/app/schemas/__init__.py` and TypeScript in `web/lib/api/types.ts` (`test_backend_schemas_match_frontend_types`). A second test fails if an endpoint is implemented but not listed here, or listed but not implemented (`test_openapi_lists_documented_endpoints`). In development the live OpenAPI document is at `/api/v1/openapi.json` (interactive docs at `/api/v1/docs`); both are disabled in production.

## Conventions

| Topic | Rule |
|---|---|
| Auth | Session cookie `stw_session` (`__Host-stw_session` in production): opaque, `HttpOnly`, `SameSite=Lax`, `Secure` in production. Idle timeout 12 h, absolute 7 days. |
| CSRF | Every `POST/PATCH/PUT/DELETE` (including login and register) needs header `X-CSRF-Token` equal to the `stw_csrf` cookie. Get both from `GET /auth/csrf`. A cross-site `Origin` is rejected. Failure → `403 csrf_failed`. |
| Tenancy | The workspace comes from the session. IDs from another workspace return `404 not_found` (same as nonexistent) and are audited as `authz.denied`. Unknown body fields are rejected (`422`). |
| Errors | `{"error": {"code", "message", "request_id", "details"}}`. `message` is safe to show users. Validation: `422 validation_error`, `details = [{"field", "message"}]`. |
| Request ID | Every response has `X-Request-ID` (echoes a valid incoming one). Shown to users as "Ref" on errors. |
| Pagination | `?page=1&page_size=N` → `{items, page, page_size, total}`. |
| Idempotency | `POST /tasks` accepts `Idempotency-Key` (≤ 80 chars). A repeat returns the original task with `200`. |
| Time | ISO-8601 UTC timestamps. |
| Rate limits | `429 rate_limited` with `Retry-After`. Limits below. |

### Rate limits

| Name | Limit |
|---|---|
| Login per IP / per email | 20 / 15 min · 10 / 15 min |
| Register per IP / per email | 5 / hour · 3 / hour |
| Forgot password per IP / per email | 5 / hour · 3 / hour |
| Resend verification per IP / per email | 5 / hour · 3 / hour |
| Reset password per IP · Verify email per IP | 10 / hour · 20 / hour |
| Task creation per user (incl. automation runs) | 30 / min |
| Uploads per user | 30 / 10 min |
| Other writes per user | 120 / min |
| Account requests per user | 3 / hour |
| Denied-ID probes per user (404s that are audited) | 30 / 10 min |
| Two-step codes per user / per IP | 10 / 15 min · 30 / 15 min |
| Password re-checks for security settings per user | 10 / 15 min |

Client IP for these limits: the peer address, or `X-Real-IP` set by Caddy when `TRUST_PROXY_HEADERS=true`. `X-Forwarded-For` is never trusted. Register, forgot-password and resend-verification always take at least `AUTH_RESPONSE_FLOOR_MS`.

### Roles

viewer = read-only (plus own preferences, onboarding, notifications). member = also create/cancel tasks, messages, uploads, deletion requests. owner/admin = also plan approve/reject, file classification/collection, automations, integrations, approval decisions, workspace settings. A missing role → `403 forbidden`.

### Common error codes

`not_authenticated`, `session_expired`, `invalid_credentials`, `csrf_failed`, `forbidden`, `not_found`, `validation_error`, `rate_limited`, `quota_exceeded`, `invalid_state_transition`, `action_not_allowed`, `invalid_or_expired_token`, `approval_expired`, `approval_already_decided`, `approval_payload_changed`, `acknowledgement_required`, `confirmation_mismatch`, `deletion_already_requested`, `unsupported_file_type`, `file_too_large`, `file_empty`, `file_quarantined`, `scanner_unavailable`, `invalid_code`, `invalid_password`, `mfa_challenge_expired`, `mfa_not_set_up`, `mfa_already_enabled`, `mfa_not_enabled`, `integration_not_available`, `integration_not_connected`, `scheduler_not_configured`, `payload_too_large`, `internal_error`.

## Endpoints

### Auth & account

| Endpoint | Body → Response | Notes |
|---|---|---|
| `GET /auth/csrf` | → `{csrf_token}` | Sets the `stw_csrf` cookie. |
| `POST /auth/register` | `{name, email, password, accept_terms}` → `202 {message}` | Same response whether or not the email exists. Sends a verification link (dev: file in `MAIL_OUTBOX_DIR`). Does **not** sign in. Password: 10–128 chars incl. a digit. |
| `POST /auth/verify-email` | `{token}` → `{message}` | Single-use, 24 h. |
| `POST /auth/resend-verification` | `{email}` → `202 {message}` | Generic response. |
| `POST /auth/login` | `{email, password}` → `Me` + session cookie | Requires a verified email. If two-step sign-in is on: → `{mfa_required: true, methods}` and a 5-minute httpOnly challenge cookie, **no session**. All failures → `401 invalid_credentials` with one generic message. Rotates to a new session. |
| `POST /auth/mfa/verify` | `{code}` → `Me` + session cookie | Second step. `code` = 6-digit authenticator code or a recovery code. Wrong → `401 invalid_code` (the challenge stays valid until it expires); expired/used/missing challenge → `401 mfa_challenge_expired`. Codes can't be reused. |
| `POST /auth/logout` | → `204` | Revokes the session server-side. |
| `POST /auth/forgot-password` | `{email}` → `202 {message}` | Generic response. Link valid 1 h, single use. |
| `POST /auth/reset-password` | `{token, password}` → `{message}` | Revokes **all** sessions of the user. |
| `GET /me` | → `Me` | `{user, workspace: {id, name, role}, demo:false}` |
| `GET /me/mfa` | → `{enabled, pending_setup, enabled_at, recovery_codes_remaining}` | Never returns the secret. |
| `POST /me/mfa/setup` | `{password}` → `{secret, otpauth_uri, qr_svg_data_uri}` | Re-authentication. Shown once; the seed is stored AES-GCM encrypted. `409 mfa_already_enabled` if on. |
| `POST /me/mfa/enable` | `{code}` → `{recovery_codes[10], message}` | Confirms setup with a current code. Signs out other sessions; e-mail notice. Codes are shown only now. |
| `POST /me/mfa/disable` | `{password, code}` → `MfaStatus` | Password **and** a current code (or recovery code). E-mail notice. |
| `POST /me/mfa/recovery-codes` | `{password, code}` → `{recovery_codes[10], message}` | Old codes stop working. |
| `POST /me/onboarding` | `{goals[], where[], ai_rules}` → `Me` | Codes are `[a-z0-9_]`, ≤ 10 each. |
| `PATCH /me/preferences` | `{name?, timezone?, notifications?}` → `Settings` | |
| `POST /account/export-request` | → `201 AccountRequest` | Recorded for manual handling. |
| `POST /account/deletion-request` | `{reason}` → `201 AccountRequest` | Recorded only; nothing is deleted. |

### Dashboard, tasks

| Endpoint | Body / query → Response | Notes |
|---|---|---|
| `GET /agent/status` | → `AgentStatus` | Model provider (label, model, `local`/`external`, what it receives), worker liveness (orchestrator, tools, deletion, scheduler), this workspace's queue depth, policy version. |
| `GET /agent/tools` | → `AgentTool[]` | The fixed tool registry with availability for this workspace (tool switches, sandbox, model). Unbuilt tools are listed with `available=false`. |
| `GET /dashboard` | → `Dashboard` | Counts, agent status (same as `/agent/status`), setup checklist, recent tasks/approvals/activity, unread count. |
| `GET /tasks` | `?q&status&type&created=today\|3d&integration=<key>\|none&sort=updated\|created\|title&page&page_size≤100` → `Page<TaskSummary>` | `q` matches title, prompt or `T-<n>`. |
| `POST /tasks` | `CreateTaskInput` → `201 Task` | Attachments must be your own ready/unscanned, non-deleted files. `integration_key` must be a connected integration. New tasks are `queued` (`waiting_for="planning_service"`) with a durable `task.plan` job. With no model configured the task waits and nothing is sent anywhere. A plan always needs approval before anything runs, in every mode; `prepare` mode never applies changes. `503 queue_full` under backpressure. |
| `GET /tasks/{task_id}` | → `Task` | `plan` (current version) with per-step `policy` decision, `impact`, `classifications`, `destinations`, `output_summary`/`output`, `error`; `artifacts`; `allowed_actions` from the state machine. |
| `POST /tasks/{task_id}/cancel` | `{reason?}` → `Task` | Allowed from any non-terminal status. Pending jobs are cancelled, pending approvals of the task expire, a running tool's result is discarded. |
| `POST /tasks/{task_id}/pause` | → `Task` | Only from `running`. |
| `POST /tasks/{task_id}/resume` | → `Task` | From `paused` → `queued`; execution continues with the next step. |
| `POST /tasks/{task_id}/messages` | `{text, kind: question\|answer\|plan_change}` → `Task` | `answer` only in `needs_input`; `plan_change` only in `awaiting_approval` with a plan — it re-plans with the text as feedback (new plan version). |
| `POST /tasks/{task_id}/plan/approve` | `{selected_step_ids[]}` → `Task` | Owner/admin. Steps blocked by policy can't be selected; unselected steps become `skipped`; task → `queued` and a `task.execute` job is queued. Steps that need approval still ask one by one when reached. |
| `POST /tasks/{task_id}/plan/reject` | `{note?}` → `Task` | → `cancelled`. |
| `GET /tasks/{task_id}/events` | `?page&page_size≤200` → `Page<TaskEvent>` | Oldest first. |

Task state machine: `api/app/domain/task_states.py` (tested for every pair). Only `running` can reach `completed`.

### Approvals

| Endpoint | Body / query → Response | Notes |
|---|---|---|
| `GET /approvals` | `?status=pending\|decided\|all&page&page_size` → `Page<Approval> + pending_count` | Overdue pending requests are marked `expired`. |
| `GET /approvals/{approval_id}` | → `Approval` | |
| `POST /approvals/{approval_id}/decision` | `{decision: approve\|reject\|request_changes, note?, acknowledged, confirmed_items?}` → `Approval` | Owner/admin only. `approve` needs `acknowledged=true`; for `record_deletion` also `confirmed_items` equal to `affected_items`. `request_changes` needs a note. One decision per request (`409` after). The request is HMAC-signed over everything shown plus `operation`, `args_hash`, `targets`, `step_id`, expiry and `policy_version`. Approving sets `execution_state="pending_execution"` and queues the job; the worker re-verifies the binding and the approver's role right before running, uses it once (`executed`), or marks it `invalidated` if anything changed. Declining a step skips it; declining a planning transfer cancels the task. |

### Files

| Endpoint | Body / query → Response | Notes |
|---|---|---|
| `GET /files/limits` | → `UploadLimits` | Max size and allowed types. |
| `GET /files` | `?q&collection&classification&status&deleted&page&page_size≤200` → `Page<FileItem> + collections[{name,count}]` | Deleted files are hidden; `deleted=true` lists restorable ("Recently deleted") files with `restorable_until`. |
| `POST /files` | multipart `file`, `collection` → `201 FileItem` | Type detected from content (PDF, PNG, JPG, DOCX, XLSX, CSV, TXT, MD, JSON); executables, scripts, HTML/SVG and archives refused (`415`). Size limit while streaming (`413`). Stored privately under a random name. Status `unscanned` (scanner not configured), classification `unknown`. |
| `GET /files/{file_id}` | → `FileItem` | `410 file_deleted` for a deleted file. |
| `PATCH /files/{file_id}` | `{classification?, collection?}` → `FileItem` | Classification changes are audited. |
| `GET /files/{file_id}/download` | → bytes | `Content-Disposition: attachment`, `application/octet-stream`, `nosniff`, `CSP: sandbox`. Quarantined files refused. |
| `POST /files/{file_id}/deletion-request` | `{reason}` → `201 Approval` | Preview (file id, name, size, SHA-256) + `deletion_requests` row + one-time `record_deletion` approval bound to that exact content. The API never deletes: after approval the deletion service (own DB role) re-validates and soft-deletes. |
| `POST /files/{file_id}/restore` | → `202 FileItem` | Owner/admin. Restores a deleted file during the retention window (queued to the deletion service). `410 retention_ended` after it. |
| `GET /artifacts/{artifact_id}/download` | → bytes | A report produced by a task. Same download headers as files. |

### Automations, integrations

| Endpoint | Body → Response | Notes |
|---|---|---|
| `GET /automations` | → `Automation[]` | |
| `POST /automations` | `CreateAutomationInput` → `201 Automation` | Enabled on creation; scheduled ones get `next_run_at`. Each run creates a task whose plan still needs approval. |
| `PATCH /automations/{automation_id}` | `{enabled}` → `Automation` | Pausing clears the next run; enabling schedules it. The scheduler runs each slot at most once (row lock + per-slot idempotency key). |
| `POST /automations/{automation_id}/run` | → `201 Task` | Creates a task and queues planning. |
| `GET /integrations` | → `Integration[]` | Catalog with this workspace's status. `operational=true` only for real adapters (WordPress read-only; the AI provider configured by the administrator). Others are listed as not built. |
| `POST /integrations/wordpress/connect` | `{site_url, username?, application_password?}` → `202 Integration` | Owner/admin. `https://` only; the domain must already be on the allowed list. Without credentials: read-only. With a WordPress **Application Password** (both fields or neither): the agent may also create **draft** posts (tool `wordpress.create_draft`, switch `wp_write`, each draft needs a one-time approval of the exact text). The password is stored AES-256-GCM encrypted; it is never returned, logged or readable by the API role. Stays `not_connected` until the tools worker verifies `/wp-json/` and, with credentials, `users/me` with `edit_posts` (then `connected` or `error` with a message). `Integration.write_access` shows whether drafts are enabled. Disconnect wipes the stored credential. |
| `POST /integrations/{key}/connect` | → `409 integration_not_available` | Audited. For integrations that are not built (and `ai_provider`, which is configured on the server). No credentials are ever collected. |
| `POST /integrations/{key}/disconnect` | → `Integration` | Clears the stored site URL and scopes. |

### Reports, activity, notifications, security, settings

| Endpoint | Response | Notes |
|---|---|---|
| `GET /reports` | `{artifacts[], reports: Task[]}` | Reports written by `report.write` steps (`verified_result`) and finished tasks. |
| `GET /activity` | `Page<ActivityEvent>` (`?type&page&page_size`) | Reads the workspace's audit log (newest first). |
| `GET /notifications` | `{items, unread}` (`?unread=true`) | `target.route` is an app path. |
| `POST /notifications/read-all` | `204` | |
| `POST /notifications/{notification_id}/read` | `204` | |
| `GET /security/status` | `SecurityStatus` | Checklist states are computed from configuration: `enforced`, `partial` or `not_configured`. `privacy_enforced=true` (policy engine). `ai_provider_policy` states what the configured model receives. |
| `GET /settings` | `Settings` | `enforced=true`: privacy mode, tool switches and allowed domains are read by the policy engine before every step. AI rules can only make it stricter. |
| `PATCH /workspace/settings` | `Settings` | Owner/admin only. `{name?, privacy_mode?, ai_rules?, tool_permissions?, allowed_domains?}`. |
| `GET /usage` | `Usage` | Tasks/month, storage, uploads; quotas from config. |

The health check `/healthz` (GET, outside `/api/v1`, not proxied to browsers) returns `{"status":"ok"}` when the database is reachable.
