<p align="center">
  <img src="docs/images/banner.png" alt="Steward — Live Agent: a private, approval-first AI agent workspace" width="100%">
</p>

<h1 align="center">Steward · Live Agent</h1>

<p align="center">
  <b>A private, approval-first AI agent workspace.</b><br>
  Describe a task in plain language → review the plan → approve every sensitive action → see exactly what happened.
</p>

<p align="center">
  <a href="README.bn.md"><b>বাংলায় পড়ুন (Bangla guide)</b></a> ·
  <a href="#-quick-start-5-minutes">Quick start</a> ·
  <a href="#%EF%B8%8F-deploy-on-a-hostinger-vps-step-by-step">Deploy on Hostinger</a> ·
  <a href="#%EF%B8%8F-screens">Screens</a> ·
  <a href="#-what-the-agent-can-and-cannot-do">Capabilities &amp; limits</a> ·
  <a href="API_CONTRACT.md">API</a> ·
  <a href="SECURITY_AND_PRIVACY.md">Security</a>
</p>

<p align="center">
  <img alt="status" src="https://img.shields.io/badge/status-Phase%203%20agent%20engine-3448C5">
  <img alt="tests" src="https://img.shields.io/badge/backend%20tests-413%20passing-16803C">
  <img alt="e2e" src="https://img.shields.io/badge/e2e-25%2F25%20%2B%2013%2F13-16803C">
  <img alt="stack" src="https://img.shields.io/badge/Next.js%2016%20·%20FastAPI%20·%20PostgreSQL%2016-172033">
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-16803C"></a>
</p>

> [!IMPORTANT]
> **Current status: Phase 3 — the agent engine works end to end (not deployed to production).**
> A task is planned by an AI model, checked by a default-deny policy engine, approved by you (the plan, plus one-time approvals for sensitive steps), executed by isolated background workers, and fully audited.
> It plans with **any OpenAI-compatible model** you configure on the server (OpenAI, a gateway, or a self-hosted Ollama/vLLM). With **no model configured, tasks wait and nothing is sent anywhere**.
> Real tools today: read attached files, analyse CSV/text, inspect web pages and run SEO audits on **allowlisted** sites, read a WordPress site and — if you allow it — create **draft** posts that you approve word for word, write reports, summarise with the model, run short Python programs in a sandbox (with approval), and *propose* file deletions. Publishing or editing live WordPress content, sending email and full browser automation are **not built** and are shown as such. Accounts can use two-step sign-in, and uploads can be malware-scanned with ClamAV.
> No product is "completely secure"; what is enforced and tested — and what is not — is listed in [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md).

---

## Table of contents

1. [What is this, and who is it for?](#-what-is-this-and-who-is-it-for)
2. [How it works](#%EF%B8%8F-how-it-works)
3. [What the agent can and cannot do](#-what-the-agent-can-and-cannot-do)
4. [Rules, conditions and safety guarantees](#-rules-conditions-and-safety-guarantees)
5. [Screens](#%EF%B8%8F-screens)
6. [Quick start (5 minutes)](#-quick-start-5-minutes)
7. [Using the app — guide for everyday users](#-using-the-app--guide-for-everyday-users)
8. [Hosting requirements](#%EF%B8%8F-hosting-requirements)
9. [Deploy on a Hostinger VPS (step by step)](#%EF%B8%8F-deploy-on-a-hostinger-vps-step-by-step)
10. [Which APIs and keys are needed?](#-which-apis-and-keys-are-needed)
11. [Configuration reference](#-configuration-reference)
12. [For developers](#-for-developers)
13. [Testing and quality](#-testing-and-quality)
14. [Security and privacy](#-security-and-privacy)
15. [Roadmap](#%EF%B8%8F-roadmap)
16. [FAQ and troubleshooting](#-faq-and-troubleshooting)
17. [Documentation index](#-documentation-index)

---

## 💡 What is this, and who is it for?

**Steward is a control panel for an AI agent that does real work for you — but only with your permission.**

Most "AI agents" either just chat, or act on your accounts without telling you exactly what they will touch. Steward is built the other way round:

- **You describe a task in plain language** — e.g. *"Audit my WordPress website for performance and SEO problems, prepare a report, and show me recommended fixes before changing anything."*
- **The agent prepares a plan** — every step, which tool it uses, what data it reads, and how risky each step is.
- **You review and approve** — untick steps you don't want, ask questions, or reject the plan.
- **Every sensitive action needs its own one-time approval** — changing a live website, publishing, sending a message, deleting something, or sending data outside your workspace.
- **Everything is logged** — who did what, when, and why.

| You are… | Steward helps you… |
|---|---|
| A **business owner / non-technical user** | Hand off website maintenance, SEO, content and research without learning tools or command lines, while staying in control. |
| An **agency / freelancer** | Run repeatable website audits and content work for clients with a clear approval trail. |
| A **developer / operator** | Self-host a multi-tenant agent platform with real auth, tenant isolation, a policy engine, isolated workers, an audit log and a clean API. |

**Typical tasks it is designed for:** WordPress maintenance (plugin compatibility, broken links, speed), SEO and content (meta descriptions, content briefs, alt text), research (competitor pricing, schema options), file analysis (CSV/support tickets), code tasks (writing tests in a sandbox), and safe browser workflows (uptime/SSL checks on allowlisted sites).

---

## ⚙️ How it works

<p align="center"><img src="docs/images/how-it-works.png" alt="How a task flows: Describe, Plan, Review plan, Approve actions, Execute, Audit and report" width="100%"></p>

1. **Describe** — Create a task (prompt, type, priority, optional files, execution mode).
2. **Plan** — A background *orchestrator* asks the configured AI model for a plan. The model may only propose a goal, a tool from a fixed list, arguments and the expected output per step; the **server** validates everything and decides permissions, data classification, destinations and whether approval is needed. If the model is an external service, sending the task to it may itself need your approval first.
3. **Review plan** — Every step shows the policy engine's decision (*Allowed*, *Needs approval*, *Blocked by policy*) with reasons, where data goes and the exact arguments. Approve all or some steps, ask for changes (re-plans), or reject.
4. **Approve actions** — Sensitive steps (external reads in strict mode, data leaving your servers, code execution, publishing, live changes, deletion) each need a **one-time approval bound to that exact action**. It expires, and is refused if anything changed.
5. **Execute** — An isolated *tools worker* runs one step at a time, after re-checking the policy and the approval. Network access only to your allowed domains; code only in a sandbox.
6. **Audit & report** — Every decision, block and execution lands in the append-only audit log; reports are saved privately and downloadable.

### Architecture

<p align="center"><img src="docs/images/architecture.png" alt="Architecture: Browser → Caddy → Next.js web app → FastAPI API → PostgreSQL and private storage; the agent engine workers read the durable job queue" width="100%"></p>

| Part | Technology | Role |
|---|---|---|
| Web app | Next.js 16, React 19, TypeScript, Tailwind CSS 4 | All 20 screens; talks to the API through one adapter |
| API | Python 3.13, FastAPI, SQLAlchemy 2, Alembic | Auth, sessions, tenant isolation, tasks, approvals, files, audit |
| Database | PostgreSQL 16 | 24 tables incl. a durable job queue; every process uses its own restricted role (none can DELETE application rows) |
| Files | Private Docker volume | Uploads stored outside any web root under random names |
| Proxy | Caddy 2 | HTTPS certificates, security headers, real client IP |
| Agent engine | `orchestrator`, `tools`, `deletion`, `scheduler` workers (same image, `python -m app.workers <kind>`) | Planning, policy, tool execution, sandbox, deletion service, automations. Only the orchestrator and tools worker can reach the internet |

More detail: [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 🧠 What the agent can and cannot do

### Capabilities

| Capability | Status |
|---|---|
| Create tasks in plain language, with type, priority, mode, files | ✅ |
| AI plan with per-step tool, data classification, destinations and policy decision | ✅ with a configured model (OpenAI-compatible or local); without one the task waits |
| Review, partially approve, re-plan ("Edit plan") or reject plans | ✅ |
| One-time approvals bound to the exact action, re-checked before running | ✅ |
| Read and analyse attached text/CSV/Markdown/JSON files | ✅ (`files.read`, `files.analyze`) |
| Inspect a web page / SEO audit of a page | ✅ allowlisted domains only (`web.inspect`, `seo.audit`) |
| WordPress | ✅ read pages/posts (public REST API); optional **draft posts only** with an encrypted Application Password — never publishes, edits or deletes; every draft needs your approval |
| Write a markdown report, downloadable | ✅ (`report.write`) |
| Summarise results with the AI model | ✅ (`ai.summarize`); external providers need approval for confidential data |
| Run a short Python program | ✅ in a sandbox, **always with approval**; disabled automatically if the host can't isolate it |
| Delete files | ✅ only through the deletion workflow: request → your approval → separate deletion service re-checks → soft delete → restorable for 30 days → purged. The agent can only *propose* deletions |
| Automations | ✅ manual and scheduled; each run creates a task whose plan still needs your approval |
| Activity & audit log, notifications, security status, settings | ✅ |
| Publish or change live website content, send email, full browser automation, Notion/cloud storage | ❌ Not built — listed as unavailable, never faked |
| Malware scanning | ❌ Not connected — files show *Not scanned* |

### Hard limits — what the agent will never do

- ❌ Run anything **without an approved plan**, or a sensitive step without its **own** one-time approval.
- ❌ Let the AI model decide permissions, destinations or approvals — its output is untrusted input; extra fields like `"requires_approval": false` are ignored.
- ❌ Send **Unknown** (unclassified) data outside the workspace, or **Confidential** data without your approval. Uploading a file never means it may be shared.
- ❌ Contact a website that is not on your **allowed domains** list, or any private/internal network address.
- ❌ Silently switch to another AI provider, or send anything when no model is configured.
- ❌ Ask for or store your passwords or API keys in the browser. The model API key lives only in the server's environment.
- ❌ Delete anything directly, or because the model thinks it is unnecessary.
- ❌ Run model-generated code inside the web/API process, or give the agent a shell or SQL access.
- ❌ Access another user's workspace, files or history — even by guessing IDs (tested).
- ❌ Pretend something worked: blocked, failed and skipped steps are shown as such.

---

## 📜 Rules, conditions and safety guarantees

| Rule | What it means for you |
|---|---|
| **Approval is per action, once** | An approval covers exactly the action shown (tool, full arguments, destinations), once. There is no "always allow". |
| **Approvals expire and are re-checked** | Unused approvals expire (24 h). Right before running, the worker checks the action is unchanged, the policy is the same version, and the approver is still an owner/admin — otherwise it asks again. |
| **Approvals are tamper-proof** | Each request is signed (HMAC) over everything you see plus the exact operation. A database trigger keeps decisions final and approvals one-time. |
| **Privacy modes are enforced** | *Strict*: asks before contacting any site and before any data leaves. *Approval*: asks before data leaves. *Custom*: public/internal data may go to the configured model unless your AI rule says ask. Confidential data always asks; Unknown data never leaves. |
| **Deletion needs double confirmation** | You must tick that you reviewed the exact item list. |
| **Your workspace is private** | Every request is checked against your workspace on the server. Other users get "not found". |
| **Email verification required** | You can only sign in after verifying your email. |
| **Strong passwords** | At least 10 characters including a number. |
| **Rate limits** | Repeated sign-in attempts, sign-ups, password resets, uploads and task creation are limited to stop abuse. |
| **Quotas** (configurable) | Default: 500 tasks per month, 1 GB file storage per workspace, 25 MB per file. |
| **Allowed file types** | PDF, PNG, JPG, DOCX, XLSX, CSV, TXT, MD, JSON. Executables, scripts, HTML/SVG, archives and macro documents are refused. |
| **Roles** | Owner/Admin: everything. Member: create tasks, upload, request deletions. Viewer: read-only. |
| **Settings are enforced** | Privacy mode, tool switches and allowed domains are read by the policy engine before every step. AI rules can only make it stricter. |
| **Deleted files are recoverable** | 30 days by default (`DELETION_RETENTION_DAYS`), then the stored bytes are purged. |
| **Model usage limits** | Default 2,000,000 tokens and 2,000 model calls per workspace per month (configurable). |

---

## 🖼️ Screens

### The agent engine in action (real backend, real workers)

Captured by the end-to-end test `e2e/agent-flow.mjs` against a local test site, using the built-in deterministic test planner (`MODEL_PROVIDER=mock`).

| Agent status: model, where it runs, live workers | Plan review: policy decision per step, exact arguments |
|---|---|
| <img src="docs/images/phase3/01-overview-agent-status.png" alt="Overview with agent status"> | <img src="docs/images/phase3/02-plan-review-policy.png" alt="Plan review with policy decisions"> |
| **One-time approval bound to the exact action** | **Completed task with results and report** |
| <img src="docs/images/phase3/03-approval-bound-action.png" alt="Bound approval"> | <img src="docs/images/phase3/04-task-completed.png" alt="Completed task"> |
| **A site not on the allowlist is blocked by policy** | **The agent can only propose a deletion — you approve it separately** |
| <img src="docs/images/phase3/06-external-transfer-blocked.png" alt="Blocked step"> | <img src="docs/images/phase3/07-deletion-needs-separate-approval.png" alt="Deletion approval"> |
| **Recently deleted — restore within 30 days** | **Security & Privacy — enforced controls** |
| <img src="docs/images/phase3/08-recently-deleted.png" alt="Recently deleted files"> | <img src="docs/images/phase3/09-security.png" alt="Security page"> |

### Designed experience (demo) and Phase 2 screens

*Demo* = the complete designed experience with sample data (demo mode runs entirely in your browser and is labelled "DEMO MODE"). *Real (Phase 2)* = screenshots from the foundation phase, before the agent engine existed.

### Dashboard (Overview)

| Demo — full experience | Real (Phase 2) |
|---|---|
| <img src="docs/images/demo/04-dashboard.png" alt="Dashboard in demo mode"> | <img src="docs/images/real/07-overview.png" alt="Dashboard on the real backend"> |

### Plan review and approvals — the heart of the product

| Plan review (Demo) | External data transfer warning (Demo) |
|---|---|
| <img src="docs/images/demo/07-plan-review.png" alt="Plan review with risk labels"> | <img src="docs/images/demo/12-approvals-external-transfer.png" alt="Approval for sending files externally"> |
| **Live website change (Demo)** | **Deletion needs exact-item confirmation (Demo)** |
| <img src="docs/images/demo/13-approvals-production-change.png" alt="Approval for a production website change"> | <img src="docs/images/demo/14-approval-delete-confirm.png" alt="Deletion confirmation dialog"> |
| **Task waiting (Phase 2, before the engine)** | **Deletion request (Phase 2)** |
| <img src="docs/images/real/09-plan-waiting-for-planner.png" alt="Task waiting for the planning service"> | <img src="docs/images/real/14-approval-deletion.png" alt="Real deletion approval"> |

<details>
<summary><b>Tasks — create, list, details, logs</b> (click to expand)</summary>

| | |
|---|---|
| New task composer (Demo) <img src="docs/images/demo/05-new-task.png"> | Task list with filters (Demo) <img src="docs/images/demo/06-tasks-list.png"> |
| Task detail — summary (Demo) <img src="docs/images/demo/09-task-detail-summary.png"> | Task detail — steps (Demo) <img src="docs/images/demo/10-task-detail-steps.png"> |
| Run logs (Demo) <img src="docs/images/demo/11-task-detail-logs.png"> | Approve plan dialog (Demo) <img src="docs/images/demo/08-approve-plan-dialog.png"> |
| Task list (Real) <img src="docs/images/real/10-tasks.png"> | Task logs (Real) <img src="docs/images/real/11-task-detail-logs.png"> |
| New task (Real) <img src="docs/images/real/08-new-task.png"> | |
</details>

<details>
<summary><b>Files &amp; Knowledge</b></summary>

| | |
|---|---|
| Files (Demo) <img src="docs/images/demo/15-files.png"> | File details & classification (Demo) <img src="docs/images/demo/16-file-drawer.png"> |
| Real uploads — "Not scanned", "Unknown — sharing blocked" <img src="docs/images/real/12-files.png"> | File drawer (Real) <img src="docs/images/real/13-file-drawer.png"> |
| Approve-once dialog (Real) <img src="docs/images/real/15-approve-dialog.png"> | |
</details>

<details>
<summary><b>Automations, Integrations, Reports &amp; Activity</b></summary>

| | |
|---|---|
| Automations (Demo) <img src="docs/images/demo/17-automations.png"> | New automation wizard (Demo) <img src="docs/images/demo/18-automation-wizard.png"> |
| Integrations (Demo) <img src="docs/images/demo/19-integrations.png"> | Integration permissions drawer (Demo) <img src="docs/images/demo/20-integration-drawer.png"> |
| Reports & activity (Demo) <img src="docs/images/demo/21-reports-activity.png"> | Real audit log <img src="docs/images/real/17-activity-audit-log.png"> |
| Integrations (Real) <img src="docs/images/real/16-integrations.png"> | |
</details>

<details>
<summary><b>Security, Settings, Notifications, Help, System states</b></summary>

| | |
|---|---|
| Security & Privacy (Demo) <img src="docs/images/demo/22-security-privacy.png"> | Security status (Real — honest checklist) <img src="docs/images/real/18-security.png"> |
| AI behaviour settings (Demo) <img src="docs/images/demo/23-settings-ai.png"> | Settings (Real) <img src="docs/images/real/19-settings.png"> |
| Notifications (Demo) <img src="docs/images/demo/24-notifications.png"> | Notifications (Real) <img src="docs/images/real/20-notifications.png"> |
| Help & Getting Started (Demo) <img src="docs/images/demo/25-help.png"> | System states (Demo) <img src="docs/images/demo/26-system-states.png"> |
</details>

<details>
<summary><b>Sign-up, verification, onboarding, landing</b></summary>

| | |
|---|---|
| Landing page (Demo) <img src="docs/images/demo/01-landing.png"> | Register (Real) <img src="docs/images/real/01-register.png"> |
| Check your inbox (Real) <img src="docs/images/real/02-verify-email-sent.png"> | Email verified (Real) <img src="docs/images/real/03-email-verified.png"> |
| Sign in (Real) <img src="docs/images/real/04-login.png"> | Onboarding — goals (Real) <img src="docs/images/real/05-onboarding.png"> |
| Onboarding — safety approvals (Demo) <img src="docs/images/demo/03-onboarding-important-actions.png"> | Onboarding summary (Real) <img src="docs/images/real/06-onboarding-summary.png"> |
</details>

<details>
<summary><b>Mobile</b></summary>

| Dashboard | Navigation | Approvals |
|---|---|---|
| <img src="docs/images/demo/27-mobile-dashboard.png" width="260"> | <img src="docs/images/demo/28-mobile-navigation.png" width="260"> | <img src="docs/images/demo/29-mobile-approvals.png" width="260"> |
</details>

---

## 🚀 Quick start (5 minutes)

### Option A — Docker (recommended, works the same on your laptop and a VPS)

Requirements: [Docker](https://docs.docker.com/get-docker/) with Docker Compose v2, and Git.

```bash
git clone https://github.com/Mostafijemon24/Live-Agent.git
cd Live-Agent
cp .env.example .env
```

Open `.env` and replace **every** `replace-me` (six database passwords and `APPROVAL_SIGNING_KEY`). Generate strong values with:

```bash
openssl rand -base64 32     # run once per value
```

Start everything:

```bash
docker compose up -d --build
```

Open **http://localhost:3000** and register.

In development, emails (verification, password reset) are **not sent** — they are saved as files. Read the latest one:

```bash
docker compose exec api sh -c 'cat "/data/mail-outbox/$(ls -t /data/mail-outbox | head -1)"'
```

Copy the `http://localhost:3000/verify-email?token=…` link into your browser, then sign in.

**Let the agent plan (choose one in `.env`), then run `docker compose up -d` again:**

```ini
# A) Try it with the built-in deterministic test planner (development only, no AI, nothing sent anywhere)
MODEL_PROVIDER=mock

# B) A self-hosted model on your own server (e.g. Ollama with an OpenAI-compatible endpoint)
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=http://ollama:11434/v1
MODEL_NAME=llama3.1:8b
MODEL_LOCATION=local
MODEL_PROVIDER_LABEL=Ollama on this server

# C) An external AI service (data leaves your servers — confidential data needs your approval)
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_NAME=gpt-4o-mini
MODEL_API_KEY=<your key — stays on the server>
MODEL_LOCATION=external
MODEL_PROVIDER_LABEL=OpenAI
```

Then in the app: **Settings → Allowed domains** (add your website's domain so the agent may read it), pick a **privacy mode**, and create a task such as *"Run an SEO audit of https://yourdomain.com/ and write a report."*

> The `tools` worker runs Python only if it can create an isolated sandbox. Docker's default security profile prevents that, so **code steps are blocked in Docker by default** (everything else works). Don't disable seccomp to change this; run code tasks on a host with gVisor or a dedicated sandbox machine.

### Option B — Demo mode only (no database, no backend)

See the full designed experience with sample data in 1 minute (requires Node.js 22):

```bash
cd web
npm ci
NEXT_PUBLIC_DEMO_MODE=true npm run dev
```

Open http://localhost:3000 and sign in with **any** email and password (use the password `error` to see the error state). Nothing is sent anywhere; data resets on reload.

### Option C — Run each part manually (for development)

<details>
<summary>Show manual setup (PostgreSQL 16 + Python 3.13 + Node 22)</summary>

```bash
# 1) Database roles + database (once, as a PostgreSQL superuser)
sudo -u postgres STEWARD_OWNER_PASSWORD=devowner STEWARD_APP_PASSWORD=devapp \
  STEWARD_WORKER_PASSWORD=devworker STEWARD_DELETER_PASSWORD=devdeleter STEWARD_READONLY_PASSWORD=devreadonly \
  bash deploy/db/init-roles.sh

# 2) API  (uses uv: https://docs.astral.sh/uv/)
cd api
uv sync
cat > .env <<'EOF'
ENV=development
DATABASE_URL=postgresql+psycopg://steward_app:devapp@127.0.0.1:5432/steward
MIGRATION_DATABASE_URL=postgresql+psycopg://steward_owner:devowner@127.0.0.1:5432/steward
APP_ORIGIN=http://localhost:3000
WORKER_DATABASE_URL=postgresql+psycopg://steward_worker:devworker@127.0.0.1:5432/steward
DELETION_DATABASE_URL=postgresql+psycopg://steward_deleter:devdeleter@127.0.0.1:5432/steward
MODEL_PROVIDER=mock
EOF
uv run alembic upgrade head
uv run uvicorn app.main:app --port 8000 --no-proxy-headers
#   API docs (development only): http://localhost:8000/api/v1/docs

# 2b) Workers (one terminal each, or a process manager)
uv run python -m app.workers orchestrator
uv run python -m app.workers tools
uv run python -m app.workers deletion
uv run python -m app.workers scheduler

# 3) Web (new terminal)
cd web
npm ci
npm run dev          # http://localhost:3000 — proxies /api/* to :8000
```
Development emails are written to `api/var/mail-outbox/`.
</details>

---

## 🙋 Using the app — guide for everyday users

1. **Create your account** — Register with your name, email and a password (10+ characters with a number). Open the link in the verification email, then sign in.
2. **Set up your workspace (2 minutes)** — Choose your goals, where the agent may work (workspace files, connected apps, approved websites) and which actions always need your approval. Safe defaults are pre-selected.
3. **Create a task** — Go to **New task** (or use **Quick task** on the Overview). Write what you need in one or two sentences. Choose:
   - **Plan only** *(safest — default)*: the agent only prepares a plan.
   - **Prepare changes for review**: drafts changes inside your workspace; nothing is applied.
   - **Execute approved steps**: runs the steps you approve. In every mode a plan needs your approval first.
4. **Review the plan** — Each step shows its tool, risk, the policy decision (*Allowed* / *Needs approval* / *Blocked by policy*) with the reason, the data involved, where it goes and the **exact arguments**. Untick steps you don't want, use **Edit plan** to re-plan with feedback, approve or reject.
5. **Handle approvals** — The **Approvals** page lists everything waiting for you, with coloured warnings (red = data leaves your workspace or deletion; amber = live change, publish, send). Check *what*, *why*, *which items*, *destination*, *consequences* and *can it be undone*, then **Approve once**, **Reject** or **Request changes**.
6. **Add files** — **Files & Knowledge → Add file.** Files stay private. Set a classification (*Public*, *Internal*, *Confidential*, *Unknown* — Unknown never leaves the workspace). To delete, use **Request deletion** — it becomes an approval; after approval the file moves to **Recently deleted** and can be restored for 30 days.
7. **Watch progress** — **Tasks** shows every task with filters; open one to see its plan, logs, inputs, outputs and audit events. **Reports & Activity** shows the full audit log.
8. **Connect WordPress (optional)** — add your site's domain under **Settings → Allowed domains**, then **Integrations → WordPress → Connect** with the site address (read-only). To let the agent prepare **draft** posts, also add a WordPress Application Password and switch on *Settings → Tool permissions → WordPress write*.
9. **Stay safe** — **Security & Privacy** shows the agent's model and where it runs, your privacy mode, what is enforced, recent decisions and blocked actions. **Settings** holds your profile, notifications, AI behaviour, tool switches, allowed domains and usage.

**When should I use it?** Whenever you want work done on your website, content, research or files **without** giving anyone (human or AI) unchecked access — and you want a record of every decision.

---

## 🖥️ Hosting requirements

| | Minimum (small team, external or no model) | Recommended (self-hosted model or heavier use) |
|---|---|---|
| Server | VPS with 2 vCPU, 4 GB RAM, 40 GB SSD (e.g. Hostinger **KVM 2**) | 4–8 vCPU, 8–16 GB RAM, 100+ GB SSD (Hostinger **KVM 4 / KVM 8**); a local LLM needs more RAM or a GPU server |
| OS | Ubuntu 24.04 LTS (or 22.04) | Same |
| Software | Docker Engine + Docker Compose v2, Git | Same |
| Network | Public IPv4, ports 22, 80, 443 open | Same + outbound access to your AI provider |
| Domain | A domain or sub-domain pointing to the VPS (A record) | Same |
| Email | SMTP account (Hostinger Email, Google Workspace, Brevo, Amazon SES, Mailgun…) | Same |
| Backups | Daily DB dump + copy of the `storage` volume off the server | Same + tested restores |

> Shared web hosting (PHP-only) is **not** suitable — you need a VPS (or any server that runs Docker).
> The sizes above are planning estimates, not measured capacity. A local baseline (one API worker) is in [PHASE2_REPORT.md](PHASE2_REPORT.md).

---

## ☁️ Deploy on a Hostinger VPS (step by step)

1. **Buy a VPS** (KVM 2 or higher) and choose **Ubuntu 24.04**. Note the server IP.
2. **Point your domain**: in your DNS panel create an **A record** `agent.yourdomain.com → <VPS IP>`.
3. **Log in and install Docker**:
   ```bash
   ssh root@<VPS IP>
   curl -fsSL https://get.docker.com | sh
   ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw --force enable
   ```
4. **Get the code**:
   ```bash
   git clone https://github.com/Mostafijemon24/Live-Agent.git /opt/live-agent
   cd /opt/live-agent
   cp .env.example .env
   nano .env
   ```
5. **Fill in `.env` for production**:
   ```ini
   POSTGRES_SUPERUSER_PASSWORD=<random>
   STEWARD_OWNER_PASSWORD=<random>
   STEWARD_APP_PASSWORD=<random>
   STEWARD_WORKER_PASSWORD=<random>
   STEWARD_DELETER_PASSWORD=<random>
   STEWARD_READONLY_PASSWORD=<random>
   APPROVAL_SIGNING_KEY=<random, 32+ characters>
   MODEL_PROVIDER=openai_compatible   # or none
   MODEL_BASE_URL=https://api.openai.com/v1
   MODEL_NAME=gpt-4o-mini
   MODEL_API_KEY=<key>
   MODEL_LOCATION=external
   MODEL_PROVIDER_LABEL=OpenAI
   APP_ENV=production
   APP_ORIGIN=https://agent.yourdomain.com
   DOMAIN=agent.yourdomain.com
   TRUST_PROXY_HEADERS=true
   MAIL_BACKEND=smtp
   MAIL_FROM=Steward <no-reply@yourdomain.com>
   SMTP_HOST=smtp.hostinger.com
   SMTP_PORT=587
   SMTP_USERNAME=no-reply@yourdomain.com
   SMTP_PASSWORD=<your mailbox password>
   ```
   The API **refuses to start** in production if a secret is still a placeholder, the signing key is short, mail isn't SMTP, the origin isn't HTTPS, the proxy setting is missing, the mock planner is selected, an external model uses `http://`, or private-network egress is enabled — this is intentional.

   *Upgrading an existing Phase 2 server:* the database init script only runs on a fresh volume. Create the three new roles once with `docker compose exec db bash /docker-entrypoint-initdb.d/10-init-roles.sh` (it reads the passwords from the container environment and skips existing roles), then start the stack — migrations 0004/0005 apply automatically.
6. **Start with automatic HTTPS (Caddy)**:
   ```bash
   docker compose --profile tls up -d --build
   docker compose ps          # db, api, web, caddy, orchestrator, tools, deletion, scheduler
   ```
7. Open **https://agent.yourdomain.com**, register, verify your email, sign in.
8. **Backups** (daily at 03:00):
   ```bash
   crontab -e
   0 3 * * * cd /opt/live-agent && ./deploy/backup.sh /opt/steward-backups >> /var/log/steward-backup.log 2>&1
   ```
   Also copy the `storage` Docker volume (uploaded files) and `/opt/backups` to another location. Test a restore regularly.
9. **Update later**:
   ```bash
   cd /opt/live-agent && git pull && docker compose --profile tls up -d --build
   ```
   Database migrations run automatically (the one-shot `migrate` service) before the API starts.

---

## 🔑 Which APIs and keys are needed?

| What | Needed? | Notes |
|---|---|---|
| Database passwords (6) | **Yes** | Generated by you, stored only in `.env` on the server |
| `APPROVAL_SIGNING_KEY` | **Yes** | Random 32+ characters; signs approval requests |
| SMTP mailbox | **Yes in production** | For verification and password-reset emails |
| Domain + DNS | **Yes in production** | For HTTPS |
| AI model (any OpenAI-compatible `/v1/chat/completions` endpoint) | **Needed for planning** | OpenAI, an API gateway, or a self-hosted Ollama/vLLM/llama.cpp server. Configured only in server environment variables. Without it tasks wait and nothing is sent |
| WordPress | Optional | Site URL (read-only); optional Application Password for draft posts (stored encrypted) |
| Malware scanner | Optional | ClamAV runs on your server (`--profile scan`, `MALWARE_SCANNER=clamav`); signatures download from ClamAV |
| Notion, cloud storage, email sending, WordPress publishing | Not built | Shown as unavailable in the app |

**This app never asks you to paste passwords or API keys into the web interface.**

---

## 🧾 Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | — (required) | `development` or `production` |
| `APP_ORIGIN` | `http://localhost:3000` | Public URL of the web app (CSRF check, email links). `https://` in production |
| `DOMAIN` | `localhost` | Domain for Caddy's HTTPS certificate |
| `POSTGRES_SUPERUSER_PASSWORD` | — | Database superuser (used only by the DB container) |
| `STEWARD_OWNER_PASSWORD` | — | Schema-owner role (runs migrations) |
| `STEWARD_APP_PASSWORD` | — | Restricted role used by the API (no DELETE, no DDL) |
| `STEWARD_WORKER_PASSWORD` | — | Role for orchestrator/tools/scheduler (no DELETE, no secrets) |
| `STEWARD_DELETER_PASSWORD` | — | Role for the deletion service only |
| `STEWARD_READONLY_PASSWORD` | — | Read-only reporting role (no secrets) |
| `MODEL_PROVIDER` | `none` | `none` · `openai_compatible` · `mock` (development only) |
| `MODEL_BASE_URL`, `MODEL_NAME`, `MODEL_API_KEY` | — | Model endpoint, model and key (server-side only) |
| `MODEL_LOCATION` | `external` | `external` (third-party service) or `local` (you operate it) — shown to users |
| `MODEL_PROVIDER_LABEL` | — | Name shown to users, e.g. "OpenAI" |
| `MONTHLY_MODEL_TOKEN_QUOTA` | 2,000,000 | Per workspace (calls: `MONTHLY_MODEL_CALL_QUOTA`, 2,000) |
| `SANDBOX_ENABLED` | `true` | Code steps run only if the tools worker can isolate them |
| `DELETION_RETENTION_DAYS` | 30 | Deleted files are restorable for this long |
| `APPROVAL_SIGNING_KEY` | — | HMAC key for approval requests (32+ chars in production) |
| `TRUST_PROXY_HEADERS` | `false` | `true` only behind Caddy (`--profile tls`) |
| `WEB_BIND` | `127.0.0.1` | Interface the web port binds to |
| `MAIL_BACKEND` | `outbox` | `outbox` (dev files) · `smtp` · `disabled` |
| `MAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` | — | Outgoing email |
| `MAX_UPLOAD_BYTES` | 25 MB | Per-file limit |
| `WORKSPACE_STORAGE_QUOTA_BYTES` | 1 GB | Storage per workspace |
| `MONTHLY_TASK_QUOTA` | 500 | Tasks per workspace per month |
| `AUTH_RESPONSE_FLOOR_MS` | 400 | Minimum response time for sign-up/reset (prevents account guessing) |
| `NEXT_PUBLIC_DEMO_MODE` | `false` | Build-time: `true` = in-browser sample data only |

Full list with comments: [.env.example](.env.example).

---

## 👩‍💻 For developers

```
api/                FastAPI service
  app/core/         config, database, security, errors, CSRF/session deps, rate limits
  app/domain/       enums and the task state machine
  app/models.py     SQLAlchemy models (24 tables)
  app/schemas/      Pydantic request/response models
  app/engine/       agent engine: policy, planner, providers, egress, redaction, sandbox, queue,
                    orchestrator, runner, deletion, scheduler, integrations, tools/
  app/workers/      worker processes (python -m app.workers orchestrator|tools|deletion|scheduler)
  app/ports/        interfaces (planner, model provider, job queue, worker, tools, policy, storage, scanner, mailer)
  app/adapters/     private storage, mail, scanner
  app/services/     business logic, always scoped to the signed-in workspace
  app/routers/      HTTP routes (/api/v1)
  migrations/       Alembic migrations (0001 schema, 0002 privileges, 0003 tenant keys, 0004 agent engine, 0005 grant hardening)
  tests/            pytest (isolated *_test database)
web/                Next.js app
  app/              routes (public pages + the (app) shell)
  features/         one folder per screen area
  components/       UI kit + app shell
  lib/api/          AgentApi interface, HTTP adapter, demo adapter, wire types
deploy/             DB role bootstrap, backup script, Caddyfile
e2e/                browser end-to-end tests (smoke.mjs, agent-flow.mjs)
docs/images/        README screenshots, banner and diagrams
project/, chats/    the approved Phase 1 design prototype
```

**Key ideas**
- **One API adapter** (`web/lib/api`): every screen calls `api.*`. Real HTTP by default; the demo adapter is only included when built with `NEXT_PUBLIC_DEMO_MODE=true`.
- **Tenant isolation** happens server-side on every query; the workspace comes from the session, never from the request.
- **Task state machine** in `api/app/domain/task_states.py` — the only place that allows status changes (tested for all 121 combinations).
- **Agent engine**: the API only records decisions and queues jobs; workers do the work. A new tool = one `ToolDefinition` (Pydantic args schema, impact, permissions, classifications, destinations, `run`) in `api/app/engine/tools/` — the policy engine and approvals apply automatically. A new model provider = one class implementing `generate()` + `ProviderInfo`.
- **API reference**: [API_CONTRACT.md](API_CONTRACT.md) (55 endpoints, error codes, rate limits) and the live OpenAPI docs at `/api/v1/docs` in development.

---

## ✅ Testing and quality

```bash
# Backend
cd api
uv run ruff check app migrations tests && uv run ruff format --check app migrations tests
uv run mypy app
uv run pytest                     # 413 tests; creates and drops only *_test databases

# Frontend
cd ../web
npm run lint && npm run typecheck && npm test && npm run build

# End-to-end (API on :8000, workers running, `npm run build && npm start` on :3000)
cd .. && MAIL_OUTBOX_DIR=api/var/mail-outbox node e2e/smoke.mjs
SITE_URL=http://acme-demo.test:8099 node e2e/agent-flow.mjs   # needs a local test site + dev egress settings
```

Latest verified results (2026-10-24, Phase 3): 413 backend tests passing (1 skipped: a fork-bomb limit the kernel does not enforce for real root in the dev container), 39 frontend unit tests, production build, browser end-to-end: smoke 25/25 and agent flow 13/13. An independent security review of the engine found 11 issues (1 High, 5 Medium); all have fixes with regression tests, a few are partial and documented ([SECURITY_REVIEW.md](SECURITY_REVIEW.md)). Full report: [PHASE3_REPORT.md](PHASE3_REPORT.md) (Phase 2: [PHASE2_REPORT.md](PHASE2_REPORT.md)).

---

## 🔒 Security and privacy

Highlights (all enforced **and** covered by automated tests):

- Argon2id password hashing · server-side sessions in HttpOnly cookies · CSRF protection · email verification · secure password reset that signs out every session
- No account guessing: identical responses and timing for existing and unknown emails
- Strict tenant isolation (server checks + database constraints) · role-based permissions
- Restricted database role with **no DELETE** · append-only audit log
- Private, type-checked file storage · safe downloads · uploads never imply sharing
- Signed, expiring, one-time approvals bound to the exact action and re-verified before execution
- Default-deny policy engine re-checked before every step · model output treated as untrusted input
- Strict privacy: nothing goes to an external model without permission; secrets redacted; usage recorded without prompts
- Egress control (allowlist, private-address block, DNS pinning) · sandboxed code (namespaces, no network, no capabilities)
- Separate database roles per process · deletion service with re-validation, soft delete, restore and purge
- Rate limits, security headers, Content-Security-Policy, production configuration guards

Phase 5 added two-step sign-in, malware scanning and encrypted credential storage. What is **not** enforced yet (a network-level egress firewall, VM-grade sandboxing, row-level security, a tamper-evident audit chain) is listed honestly in [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md).

---

## 🗺️ Roadmap

| Phase | Status | Scope |
|---|---|---|
| **1 — Design** | ✅ Done | Clickable prototype of all 20 screens (`project/`) |
| **2 — Foundation** | ✅ Done | Real auth, workspaces, tasks, approvals, files, audit, API, tests, Docker deployment, security review |
| **3 — Agent engine** | ✅ Done (not deployed) | AI planner, policy engine, bound approvals, durable queue and workers, model gateway (OpenAI-compatible / local), tools, egress control, sandbox, deletion service with retention, scheduler, WordPress read-only, observability |
| **4 — Production hardening** | ✅ Done (not deployed) | Authz/tenant test matrix, health/metrics, load tests, verified backup & restore, Hostinger deployment guide, CI |
| **5 — Features & scaling** | ✅ Done (not deployed) | Two-step sign-in (TOTP + recovery codes), ClamAV malware scanning, WordPress draft posts with encrypted credentials, 2 API containers with zero-downtime rolling restarts, orchestrator replicas |
| **Next** | 🔜 | Workspace-wide MFA requirement, email/cloud integrations, VM-grade sandbox, network egress proxy, hash-chained audit log, row-level security, real VPS deployment |

---

## ❓ FAQ and troubleshooting

<details><summary><b>I created a task but nothing happens.</b></summary>
Check <b>Overview → Agent status</b>. "No AI model is configured" means <code>MODEL_PROVIDER</code> is <code>none</code> — tasks wait and nothing is sent. "Workers not running" means the <code>orchestrator</code>/<code>tools</code> containers are down (<code>docker compose ps</code>, <code>docker compose logs orchestrator</code>). If the task waits for <i>approval</i>, open <b>Approvals</b>.</details>

<details><summary><b>A step says "Blocked by policy".</b></summary>
Read the reason under the step. Common causes: the website's domain is not in <b>Settings → Allowed domains</b>, a tool is switched off in <b>Settings → Tool permissions</b>, or the data is <i>Unknown</i> and would leave the workspace. Change the setting and use <b>Edit plan</b> to re-plan.</details>

<details><summary><b>Code steps are always blocked.</b></summary>
The sandbox could not isolate programs on the tools worker (Docker's default security profile prevents it). This is deliberate: code never runs without isolation.</details>

<details><summary><b>I didn't receive the verification email.</b></summary>
In development (<code>MAIL_BACKEND=outbox</code>) emails are saved as files, not sent — see the Quick start command to read them. In production check your SMTP settings and the API logs: <code>docker compose logs api</code>.</details>

<details><summary><b>"That email and password combination did not match, or the email address hasn't been verified yet."</b></summary>
For security the app never says which part is wrong. Make sure you verified your email; otherwise use <b>Forgot password</b>.</details>

<details><summary><b>"Too many attempts."</b></summary>
Rate limits protect accounts. Wait 15–60 minutes (the response includes a <code>Retry-After</code> time).</details>

<details><summary><b>The API container keeps restarting in production.</b></summary>
Run <code>docker compose logs api</code>. The most common cause is the production safety check: a <code>replace-me</code> secret, a short <code>APPROVAL_SIGNING_KEY</code>, <code>MAIL_BACKEND</code> not <code>smtp</code>, <code>APP_ORIGIN</code> not <code>https://</code>, or <code>TRUST_PROXY_HEADERS</code> not <code>true</code>.</details>

<details><summary><b>My upload was refused.</b></summary>
Only PDF, PNG, JPG, DOCX, XLSX, CSV, TXT, MD and JSON up to 25 MB are accepted, the file extension must match the real content, and files containing scripts, macros or executables are refused.</details>

<details><summary><b>Can I connect my WordPress site?</b></summary>
Yes: add the domain to Allowed domains, then Integrations → WordPress → Connect with the site address (read-only). Optionally add an Application Password so the agent can create **draft** posts — each one needs your approval of the exact text, and you publish it yourself in WordPress.</details>

<details><summary><b>Is my data sent to an AI company?</b></summary>
Only if your administrator configured an <i>external</i> model — the app says so on the Overview and Security pages. Even then, confidential data is sent only after your one-time approval, unknown data never, and known secrets are redacted first. With a <i>local</i> model, data stays on your own servers (it still leaves this app's process). With no model configured, nothing is sent.</details>

---

## 📚 Documentation index

| Document | Contents |
|---|---|
| [README.bn.md](README.bn.md) | Full guide in Bangla (বাংলা) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Architecture decisions |
| [API_CONTRACT.md](API_CONTRACT.md) | Every endpoint, error codes, rate limits, roles |
| [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) | Enforced vs. not-yet-enforced controls |
| [SECURITY_REVIEW.md](SECURITY_REVIEW.md) | Independent security review and fixes |
| [PRODUCTION_READINESS_REPORT.md](PRODUCTION_READINESS_REPORT.md) | Phase 4: findings, test results, Go/No-Go |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Hostinger VPS deployment, backups, releases, rollback |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Health, metrics, alerts, telemetry policy |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | Load-test results, SLOs, scaling and restart measurements, when to scale |
| [PHASE3_REPORT.md](PHASE3_REPORT.md) | Agent engine: what was built, tests, review findings, limitations and risks |
| [PHASE2_REPORT.md](PHASE2_REPORT.md) | Foundation: what was built, commands run, results, load baseline |
| [web/FRONTEND_GUIDE.md](web/FRONTEND_GUIDE.md) | Conventions for screens and the API adapter |
| [docs/DESIGN_HANDOFF.md](docs/DESIGN_HANDOFF.md), `project/` | The approved Phase 1 design |
| [LICENSE](LICENSE) | MIT license |

**License:** [MIT](LICENSE) © 2026 Mostafijemon24 — free to use, modify and distribute, including commercially, as long as the copyright notice is kept. Provided "as is", without warranty.
