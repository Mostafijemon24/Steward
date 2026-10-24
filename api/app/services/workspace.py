"""Dashboard, activity, notifications, integrations, automations, security status, settings, account requests."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select, update

from app import schemas as S
from app.core.config import get_settings
from app.core.db import utcnow
from app.core.deps import Context
from app.core.errors import ApiError
from app.domain.enums import IntegrationStatus, TaskStatus, WorkspaceRole
from app.models import (
    AccountRequest,
    ApprovalRequest,
    Artifact,
    AuditEvent,
    Automation,
    IntegrationConnection,
    Notification,
    Task,
    Workspace,
)
from app.services.agent import agent_status
from app.services.views import activity_out, approvals_out, task_full, task_ref, task_summary

# ------------------------------------------------------------------ integrations catalog
CATALOG: list[dict[str, object]] = [
    {
        "key": "wordpress",
        "name": "WordPress",
        "icon": "globe",
        "description": "Read pages, posts and plugins. Changes only after a separate approval.",
        "destination": "Your WordPress website",
        "scopes": [
            "Read pages and posts",
            "Read plugin and theme list",
            "Prepare changes (not applied)",
            "Write changes — only with per-action approval",
        ],
    },
    {
        "key": "notion",
        "name": "Notion",
        "icon": "notebook",
        "description": "Import tasks from one selected database, read-only.",
        "destination": "Notion workspace you choose",
        "scopes": ["Read one selected database"],
    },
    {
        "key": "cloud_storage",
        "name": "Cloud storage",
        "icon": "cloud",
        "description": "Read files from folders you choose. No upload without approval.",
        "destination": "Your cloud storage account",
        "scopes": ["Read selected folders", "Upload — only with per-action approval"],
    },
    {
        "key": "email",
        "name": "Email",
        "icon": "mail",
        "description": "Send messages you approve one by one. Cannot read your inbox.",
        "destination": "Recipients you approve",
        "scopes": ["Send approved messages"],
    },
    {
        "key": "browser",
        "name": "Browser automation",
        "icon": "monitor",
        "description": "Opens allowlisted websites in an isolated browser.",
        "destination": "Allowlisted domains only",
        "scopes": ["Visit allowlisted domains", "Fill forms — only with approval"],
        "backend_required": True,
    },
    {
        "key": "ai_provider",
        "name": "AI model provider",
        "icon": "cpu",
        "description": "Processes task text. Files are sent only with approval.",
        "destination": "Provider chosen by your administrator",
        "scopes": ["Process task text", "Process files — per-action approval"],
        "backend_required": True,
    },
]
CATALOG_KEYS = {str(c["key"]) for c in CATALOG}


def list_integrations(ctx: Context) -> list[S.IntegrationOut]:
    from app.engine.integrations import INTEGRATIONS
    from app.engine.providers import get_provider

    rows = {
        r.key: r
        for r in ctx.db.execute(
            select(IntegrationConnection).where(IntegrationConnection.workspace_id == ctx.workspace.id)
        ).scalars()
    }
    out = []
    for c in CATALOG:
        r = rows.get(str(c["key"]))
        key = str(c["key"])
        meta = INTEGRATIONS.get(key, {"operational": False, "mode": "not built"})
        default = IntegrationStatus.backend_required if c.get("backend_required") else IntegrationStatus.not_connected
        status = IntegrationStatus(r.status) if r else default
        description = str(c["description"])
        destination = str(c["destination"])
        scopes = [str(x) for x in c["scopes"]]  # type: ignore[attr-defined]
        if key == "ai_provider":
            info = get_provider().info
            status = IntegrationStatus.connected if info.configured else IntegrationStatus.not_connected
            description = info.data_disclosure
            destination = f"{info.label} ({info.location})" if info.configured else "None configured"
        if key == "wordpress":
            write = bool(r and (r.config or {}).get("mode") == "read_write_drafts")
            scopes = (
                [
                    "Read published pages and posts (public REST API)",
                    "Create DRAFT posts only — never publishes, edits or deletes",
                    "Every draft needs your one-time approval of the exact text",
                    "Application Password stored encrypted; revoke it any time in WordPress",
                ]
                if write
                else [
                    "Read published pages and posts (public REST API)",
                    "No credentials stored",
                    "No changes to your site",
                ]
            )
        last = "Never" if not r or not r.last_activity_at else r.last_activity_at.strftime("%b %d, %H:%M UTC")
        out.append(
            S.IntegrationOut(
                key=key,
                name=str(c["name"]),
                icon=str(c["icon"]),
                status=status,
                description=description,
                last_activity=last,
                destination=destination,
                scopes=scopes,
                connectable=key == "wordpress",
                operational=bool(meta["operational"]),
                mode=str(meta["mode"]),
                site_url=str((r.config or {}).get("site_url")) if r and (r.config or {}).get("site_url") else None,
                error_message=r.error_message if r else None,
                write_access=bool(key == "wordpress" and r and (r.config or {}).get("mode") == "read_write_drafts"),
            )
        )
    return out


def connect_integration(ctx: Context, key: str) -> S.IntegrationOut:
    if key not in CATALOG_KEYS:
        raise ApiError(404, "not_found", "That integration was not found.")
    ctx.require_role(WorkspaceRole.owner, WorkspaceRole.admin)
    if key == "wordpress":
        raise ApiError(422, "site_url_required", "Enter your site's address to connect WordPress (read-only).")
    ctx.log(
        "integration.connect_unavailable",
        "integration",
        f"Connecting {key} was requested — this integration is not built yet",
        target_type="integration",
        target_id=key,
    )
    ctx.db.commit()
    if key == "ai_provider":
        raise ApiError(
            409,
            "integration_not_available",
            "The AI model provider is configured by the server administrator (environment variables), not from the browser. API keys are never entered here.",
        )
    raise ApiError(
        409,
        "integration_not_available",
        "This integration is not built yet. WordPress (read-only) is the first available integration. This app never asks for passwords or API keys.",
    )


def disconnect_integration(ctx: Context, key: str) -> S.IntegrationOut:
    if key not in CATALOG_KEYS:
        raise ApiError(404, "not_found", "That integration was not found.")
    ctx.require_role(WorkspaceRole.owner, WorkspaceRole.admin)
    r = ctx.db.execute(
        select(IntegrationConnection)
        .where(IntegrationConnection.workspace_id == ctx.workspace.id, IntegrationConnection.key == key)
        .with_for_update()
    ).scalar_one_or_none()
    if r is None or (r.status == IntegrationStatus.not_connected.value and not (r.config or {})):
        raise ApiError(409, "integration_not_connected", "That integration is not connected.")
    r.status = IntegrationStatus.not_connected.value
    r.granted_scopes = []
    r.config = {}
    from app.services.agent import revoke_integration_secret

    revoke_integration_secret(ctx, key)  # stored credentials are wiped (cannot be restored)
    r.error_message = None
    r.updated_at = utcnow()
    ctx.log("integration.disconnected", "integration", f"{key} disconnected", target_type="integration", target_id=key)
    return next(i for i in list_integrations(ctx) if i.key == key)


# ------------------------------------------------------------------ dashboard
def dashboard(ctx: Context) -> S.DashboardOut:
    db, ws = ctx.db, ctx.workspace.id
    counts: dict[str, int] = dict(
        db.execute(select(Task.status, func.count()).where(Task.workspace_id == ws).group_by(Task.status))
        .tuples()
        .all()
    )

    def c(*st: TaskStatus) -> int:
        return int(sum(counts.get(s.value, 0) for s in st))

    week_ago = utcnow() - timedelta(days=7)
    completed_week = db.execute(
        select(func.count())
        .select_from(Task)
        .where(Task.workspace_id == ws, Task.status == "completed", Task.updated_at >= week_ago)
    ).scalar_one()
    pending = list(
        db.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.workspace_id == ws, ApprovalRequest.status == "pending")
            .order_by(ApprovalRequest.requested_at.desc())
        ).scalars()
    )
    recent = (
        db.execute(select(Task).where(Task.workspace_id == ws).order_by(Task.updated_at.desc()).limit(5))
        .scalars()
        .all()
    )
    acts = list(
        db.execute(
            select(AuditEvent).where(AuditEvent.workspace_id == ws).order_by(AuditEvent.seq.desc()).limit(6)
        ).scalars()
    )
    unread = db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.workspace_id == ws, Notification.user_id == ctx.user.id, Notification.read_at.is_(None))
    ).scalar_one()
    any_integration = (
        db.execute(
            select(func.count())
            .select_from(IntegrationConnection)
            .where(IntegrationConnection.workspace_id == ws, IntegrationConnection.status == "connected")
        ).scalar_one()
        > 0
    )
    return S.DashboardOut(
        agent=agent_status(ctx),
        counts=S.DashboardCounts(
            in_progress=c(TaskStatus.queued, TaskStatus.planning, TaskStatus.running),
            awaiting_approval_plans=c(TaskStatus.awaiting_approval),
            pending_actions=len(pending),
            completed_week=int(completed_week),
            needs_attention=c(TaskStatus.failed, TaskStatus.needs_input, TaskStatus.paused),
        ),
        setup=S.SetupOut(
            verify_email=ctx.user.email_verified_at is not None,
            privacy=ctx.workspace.privacy_configured_at is not None,
            integration=any_integration,
            first_task=sum(counts.values()) > 0,
        ),
        recent_tasks=[task_summary(t) for t in recent],
        pending_approvals=approvals_out(db, ws, pending[:3]),
        recent_activity=activity_out(db, ws, acts),
        unread_notifications=int(unread),
    )


# ------------------------------------------------------------------ activity & reports
def list_activity(ctx: Context, type_: str | None, page: int, page_size: int) -> S.Page[S.ActivityOut]:
    conds = [AuditEvent.workspace_id == ctx.workspace.id]
    if type_:
        conds.append(AuditEvent.category == type_)
    total = ctx.db.execute(select(func.count()).select_from(AuditEvent).where(*conds)).scalar_one()
    rows = list(
        ctx.db.execute(
            select(AuditEvent)
            .where(*conds)
            .order_by(AuditEvent.seq.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars()
    )
    return S.Page[S.ActivityOut](
        items=activity_out(ctx.db, ctx.workspace.id, rows), page=page, page_size=page_size, total=total
    )


def reports(ctx: Context) -> S.ReportsOut:
    ws = ctx.workspace.id
    arts = (
        ctx.db.execute(
            select(Artifact).where(Artifact.workspace_id == ws).order_by(Artifact.created_at.desc()).limit(100)
        )
        .scalars()
        .all()
    )
    nums: dict[uuid.UUID, int] = (
        dict(
            ctx.db.execute(
                select(Task.id, Task.number).where(
                    Task.workspace_id == ws, Task.id.in_([a.task_id for a in arts if a.task_id])
                )
            )
            .tuples()
            .all()
        )
        if arts
        else {}
    )
    tasks = (
        ctx.db.execute(
            select(Task)
            .where(Task.workspace_id == ws, Task.report.is_not(None))
            .order_by(Task.updated_at.desc())
            .limit(50)
        )
        .scalars()
        .all()
    )
    return S.ReportsOut(
        artifacts=[
            S.ArtifactOut(
                id=a.id,
                task_id=a.task_id,
                task_ref=task_ref(nums[a.task_id]) if a.task_id in nums else None,
                name=a.name,
                kind=a.kind,
                provenance=a.provenance,
                created_at=a.created_at,
            )
            for a in arts
        ],
        reports=[task_full(ctx.db, t) for t in tasks],
    )


# ------------------------------------------------------------------ notifications
def list_notifications(ctx: Context, unread_only: bool) -> S.NotificationList:
    conds = [Notification.workspace_id == ctx.workspace.id, Notification.user_id == ctx.user.id]
    unread = ctx.db.execute(
        select(func.count()).select_from(Notification).where(*conds, Notification.read_at.is_(None))
    ).scalar_one()
    if unread_only:
        conds.append(Notification.read_at.is_(None))
    rows = (
        ctx.db.execute(select(Notification).where(*conds).order_by(Notification.created_at.desc()).limit(100))
        .scalars()
        .all()
    )
    return S.NotificationList(
        items=[
            S.NotificationOut(
                id=n.id,
                type=n.type,
                icon=n.icon,
                text=n.text,
                created_at=n.created_at,
                unread=n.read_at is None,
                target=n.target,
            )
            for n in rows
        ],
        unread=int(unread),
    )


def mark_notification(ctx: Context, nid: uuid.UUID) -> None:
    n = ctx.db.execute(
        select(Notification).where(
            Notification.workspace_id == ctx.workspace.id, Notification.user_id == ctx.user.id, Notification.id == nid
        )
    ).scalar_one_or_none()
    if n is None:
        raise ctx.deny("notification", nid, "notification")
    if n.read_at is None:
        n.read_at = utcnow()


def mark_all_notifications(ctx: Context) -> None:
    ctx.db.execute(
        update(Notification)
        .where(
            Notification.workspace_id == ctx.workspace.id,
            Notification.user_id == ctx.user.id,
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )


# ------------------------------------------------------------------ automations
def _auto_out(a: Automation) -> S.AutomationOut:
    if a.trigger == "manual":
        when, nxt = "When you start it", "Runs when you start it"
    else:
        t = a.time_of_day or "08:00"
        when = {"weekly": f"Every {a.day or 'Monday'} at {t}", "daily": f"Every day at {t}"}.get(
            a.frequency or "", f"1st of each month at {t}"
        )
        nxt = (
            a.next_run_at.strftime("%b %d, %H:%M UTC") + " (creates a plan for review)"
            if a.enabled and a.next_run_at
            else "Paused"
        )
    last = a.last_run_at.strftime("%b %d, %H:%M UTC") if a.last_run_at else "Never run"
    return S.AutomationOut(
        id=a.id,
        ref=f"AU-{a.number}",
        name=a.name,
        trigger=a.trigger,
        schedule_text=when,
        timezone=a.timezone,
        template=a.template,
        tools=list(a.tools),
        file_scope=a.file_scope,
        approval_policy=a.approval_policy,
        last_run_text=last,
        next_run_text=nxt,
        enabled=a.enabled,
        created_at=a.created_at,
    )


def list_automations(ctx: Context) -> list[S.AutomationOut]:
    rows = (
        ctx.db.execute(
            select(Automation).where(Automation.workspace_id == ctx.workspace.id).order_by(Automation.number.desc())
        )
        .scalars()
        .all()
    )
    return [_auto_out(a) for a in rows]


def get_automation(ctx: Context, aid: uuid.UUID) -> Automation:
    a = ctx.db.execute(
        select(Automation).where(Automation.workspace_id == ctx.workspace.id, Automation.id == aid).with_for_update()
    ).scalar_one_or_none()
    if a is None:
        raise ctx.deny("automation", aid, "automation")
    return a


def create_automation(ctx: Context, body: S.CreateAutomationIn) -> S.AutomationOut:
    if body.trigger == "schedule" and not body.frequency:
        raise ApiError(
            422,
            "validation_error",
            "Some fields need attention.",
            details=[{"field": "frequency", "message": "Choose how often it runs."}],
        )
    num = int(
        ctx.db.execute(
            update(Workspace)
            .where(Workspace.id == ctx.workspace.id)
            .values(automation_seq=Workspace.automation_seq + 1)
            .returning(Workspace.automation_seq)
        ).scalar_one()
    )
    a = Automation(
        workspace_id=ctx.workspace.id,
        number=num,
        name=body.name,
        trigger=body.trigger,
        frequency=body.frequency if body.trigger == "schedule" else None,
        day=body.day if body.trigger == "schedule" and body.frequency == "weekly" else None,
        time_of_day=(body.time or "08:00") if body.trigger == "schedule" else None,
        timezone=ctx.user.timezone,
        template=body.template,
        tools=body.tools,
        file_scope=body.file_scope,
        approval_policy=body.approval_policy,
        enabled=True,
        created_by=ctx.user.id,
    )
    from app.engine.scheduler import next_run

    a.next_run_at = next_run(a, utcnow())
    ctx.db.add(a)
    ctx.db.flush()
    ctx.log(
        "automation.created",
        "task",
        f'Automation "{a.name}" created',
        target_type="automation",
        target_id=str(a.id),
    )
    return _auto_out(a)


def set_automation_enabled(ctx: Context, aid: uuid.UUID, enabled: bool) -> S.AutomationOut:
    from app.engine.scheduler import next_run

    a = get_automation(ctx, aid)
    a.enabled = enabled
    a.next_run_at = next_run(a, utcnow()) if enabled else None
    a.updated_at = utcnow()
    ctx.log(
        "automation.updated",
        "task",
        f'Automation "{a.name}" {"enabled" if enabled else "paused"}',
        target_type="automation",
        target_id=str(a.id),
    )
    return _auto_out(a)


def run_automation(ctx: Context, aid: uuid.UUID) -> S.TaskOut:
    from app.domain.enums import ExecutionMode, TaskPriority, TaskType
    from app.services.tasks import create_task

    a = get_automation(ctx, aid)
    if not a.enabled:
        raise ApiError(409, "automation_paused", "This automation is paused.")
    t_type = TaskType.website if ("WordPress" in a.template or "Plugin" in a.template) else TaskType.seo_content
    out, _ = create_task(
        ctx,
        S.CreateTaskIn(
            title=a.name,
            prompt=f'Run automation "{a.name}" using template {a.template}.',
            type=t_type,
            priority=TaskPriority.normal,
            mode=ExecutionMode.plan_only,
        ),
        None,
    )
    ctx.db.execute(
        update(Task).where(Task.workspace_id == ctx.workspace.id, Task.id == out.id).values(automation_id=a.id)
    )
    a.last_run_at = utcnow()
    ctx.log(
        "automation.run",
        "task",
        f'Automation "{a.name}" queued {out.ref}',
        target_type="automation",
        target_id=str(a.id),
        task_id=out.id,
    )
    return out


# ------------------------------------------------------------------ security status
def security_status(ctx: Context) -> S.SecurityStatusOut:
    settings = get_settings()
    ws = ctx.workspace
    blocked = (
        ctx.db.execute(
            select(AuditEvent)
            .where(
                AuditEvent.workspace_id == ws.id,
                AuditEvent.action.in_(("policy.blocked", "authz.denied", "authz.role_denied")),
            )
            .order_by(AuditEvent.seq.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    decided = list(
        ctx.db.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.workspace_id == ws.id, ApprovalRequest.status != "pending")
            .order_by(ApprovalRequest.decided_at.desc().nulls_last())
            .limit(4)
        ).scalars()
    )
    cookie_ok = settings.cookie_secure
    from app.engine.providers import get_provider

    info = get_provider(settings).info
    _, sandbox_reason = _sandbox_status()
    checklist = [
        S.SecurityCheckOut(
            key="session_cookies",
            label="Secure, HttpOnly session cookies",
            status="enforced" if cookie_ok else "partial",
            detail="HttpOnly, SameSite=Lax, server-side sessions with idle and absolute expiry."
            + ("" if cookie_ok else " The Secure flag is off because this is a development environment (no HTTPS)."),
        ),
        S.SecurityCheckOut(
            key="csrf",
            label="CSRF protection",
            status="enforced",
            detail="Double-submit token and Origin check on every change.",
        ),
        S.SecurityCheckOut(
            key="rate_limits",
            label="Rate limiting on sign-in and task creation",
            status="enforced" if settings.rate_limit_enabled else "not_configured",
            detail="Sign-in, registration, password reset, uploads and task creation are limited.",
        ),
        S.SecurityCheckOut(
            key="tenant_isolation",
            label="Tenant isolation checked on every request",
            status="enforced",
            detail="Every query is scoped to your workspace; cross-workspace IDs return 'not found' and are logged.",
        ),
        S.SecurityCheckOut(
            key="secrets",
            label="Secrets kept server-side",
            status="enforced",
            detail="The AI provider key lives only in server environment variables and is never sent to the browser, the model or logs. "
            "Integration credentials (WordPress Application Passwords) are stored AES-256-GCM encrypted with a key kept only in the server environment. The API can store them but never read them back; only the tools worker decrypts them, right before a call.",
        ),
        S.SecurityCheckOut(
            key="backups",
            label="Automated backups with tested restore",
            status="not_configured",
            detail="A backup script is documented; automated, restore-tested backups must be set up on the server.",
        ),
        S.SecurityCheckOut(
            key="audit_log",
            label="Tamper-evident audit log",
            status="partial",
            detail="Append-only: the app cannot edit or delete audit records. Hash-chaining is planned.",
        ),
        S.SecurityCheckOut(
            key="egress",
            label="Outbound network policy",
            status="enforced",
            detail="Agent tools reach only allowlisted domains; private and internal addresses are blocked, connections are pinned to the "
            "checked address, redirects to other hosts are refused, and size/time limits apply. A network-level firewall is still recommended in production.",
        ),
        S.SecurityCheckOut(
            key="deletion",
            label="Deletion protection and retention rules",
            status="enforced",
            detail=f"Deleting needs a one-time owner/admin approval of the exact file. A separate deletion service with its own database role "
            f"re-checks everything right before deleting. Deleted files can be restored for {settings.deletion_retention_days} days, then their bytes are purged.",
        ),
        S.SecurityCheckOut(
            key="policy_engine",
            label="Policy engine and one-time approvals",
            status="enforced",
            detail="Every plan step is checked against your privacy mode, tool switches and allowed domains when planned and again right before it runs. "
            "Approvals are bound to the exact action, expire, and are used once.",
        ),
        S.SecurityCheckOut(
            key="sandbox",
            label="Isolated code execution",
            status="partial",
            detail=sandbox_reason,
        ),
        S.SecurityCheckOut(
            key="mfa",
            label="Two-step sign-in",
            status="partial",
            detail="Available for every account (authenticator app + single-use recovery codes) in Settings → Security. "
            "It is optional per user; a workspace-wide requirement is not built yet.",
        ),
        S.SecurityCheckOut(
            key="malware_scan",
            label="Malware scanning for uploads",
            status="enforced" if settings.malware_scanner == "clamav" else "not_configured",
            detail=(
                "Every upload is scanned by ClamAV before it is stored. Infected files are quarantined and can't be "
                "opened; if the scanner is unavailable, uploads are refused rather than accepted unscanned."
                if settings.malware_scanner == "clamav"
                else "Files are type-checked and stored privately, but marked 'Not scanned' until a scanner is connected."
            ),
        ),
    ]
    return S.SecurityStatusOut(
        privacy_mode=ws.privacy_mode,
        privacy_enforced=True,
        checklist=checklist,
        blocked_actions=[
            S.BlockedActionOut(
                id=b.id,
                created_at=b.created_at,
                text=b.message,
                reason=str(b.meta.get("reason") or "Outside this workspace or your role"),
            )
            for b in blocked
        ],
        recent_decisions=approvals_out(ctx.db, ws.id, decided),
        retention=[
            S.LabelValue(label="Audit events", value="Kept indefinitely (append-only)"),
            S.LabelValue(
                label="Files",
                value=f"Kept until you delete them. Deleted files can be restored for {settings.deletion_retention_days} days, then removed.",
            ),
            S.LabelValue(
                label="Sessions",
                value=f"Expire after {settings.session_idle_hours} h idle or {settings.session_absolute_days} days",
            ),
            S.LabelValue(label="Password reset links", value="Expire after 1 hour, single use"),
        ],
        ai_provider_policy=info.data_disclosure + " Uploading a file never authorizes sharing it.",
    )


def _sandbox_status() -> tuple[bool, str]:
    """The API cannot probe the tools worker's sandbox; report the last status the worker published."""
    return (
        False,
        "Code runs only in the tools worker's sandbox (no network, read-only system, resource limits). "
        "Whether it is available depends on that worker's host; if it is not, code steps are blocked.",
    )


# ------------------------------------------------------------------ settings
def settings_out(ctx: Context) -> S.SettingsOut:
    ws = ctx.workspace
    from app.services.auth import DEFAULT_AI_RULES, DEFAULT_NOTIFICATIONS, DEFAULT_TOOLS

    return S.SettingsOut(
        profile=S.ProfileOut(name=ctx.user.name, email=ctx.user.email, timezone=ctx.user.timezone),
        notifications={**DEFAULT_NOTIFICATIONS, **(ctx.user.notification_prefs or {})},
        workspace=S.WorkspaceSettingsOut(
            name=ws.name,
            privacy_mode=ws.privacy_mode,
            ai_rules={**DEFAULT_AI_RULES, **(ws.ai_rules or {})},
            tool_permissions={**DEFAULT_TOOLS, **(ws.tool_permissions or {})},
            allowed_domains=list(ws.allowed_domains or []),
            can_edit=ctx.role in (WorkspaceRole.owner, WorkspaceRole.admin),
        ),
        enforced=True,
    )


def update_preferences(ctx: Context, body: S.PreferencesIn) -> S.SettingsOut:
    u = ctx.user
    changed = []
    if body.name is not None and body.name != u.name:
        u.name = body.name
        changed.append("name")
    if body.timezone is not None and body.timezone != u.timezone:
        u.timezone = body.timezone
        changed.append("timezone")
    if body.notifications is not None:
        patch = body.notifications.model_dump(exclude_none=True)
        if patch:
            u.notification_prefs = {**(u.notification_prefs or {}), **patch}
            changed.append("notifications")
    if changed:
        u.updated_at = utcnow()
        ctx.log(
            "settings.preferences_updated",
            "settings",
            "Profile preferences updated: " + ", ".join(changed),
            metadata={"fields": changed},
        )
    return settings_out(ctx)


def update_workspace_settings(ctx: Context, body: S.WorkspaceSettingsIn) -> S.SettingsOut:
    ctx.require_role(WorkspaceRole.owner, WorkspaceRole.admin)
    ws = ctx.db.execute(select(Workspace).where(Workspace.id == ctx.workspace.id).with_for_update()).scalar_one()
    changed = []
    if body.name is not None and body.name != ws.name:
        ws.name = body.name
        changed.append("name")
    if body.privacy_mode is not None:
        if body.privacy_mode.value != ws.privacy_mode:
            ctx.log(
                "privacy.mode_changed",
                "security",
                f'Privacy mode set to "{body.privacy_mode.value}" (enforced by the policy engine)',
                metadata={"from": ws.privacy_mode, "to": body.privacy_mode.value},
            )
            ws.privacy_mode = body.privacy_mode.value
        ws.privacy_configured_at = ws.privacy_configured_at or utcnow()
        changed.append("privacy_mode")
    if body.ai_rules is not None:
        patch = body.ai_rules.model_dump(exclude_none=True, by_alias=True)
        if patch:
            ws.ai_rules = {**(ws.ai_rules or {}), **patch}
            ctx.log(
                "privacy.ai_rules_changed",
                "security",
                "AI behaviour rules updated (they can only make the policy stricter)",
                metadata=patch,
            )
            changed.append("ai_rules")
    if body.tool_permissions is not None:
        patch = body.tool_permissions.model_dump(exclude_none=True)
        if patch:
            ws.tool_permissions = {**(ws.tool_permissions or {}), **patch}
            ctx.log(
                "privacy.tool_permissions_changed",
                "security",
                "Tool permissions updated (enforced before every step)",
                metadata=patch,
            )
            changed.append("tool_permissions")
    if body.allowed_domains is not None:
        ws.allowed_domains = body.allowed_domains
        ctx.log(
            "privacy.allowlist_changed",
            "security",
            f"Allowed domains updated ({len(body.allowed_domains)})",
            metadata={"count": len(body.allowed_domains)},
        )
        changed.append("allowed_domains")
    if changed:
        ws.updated_at = utcnow()
        if "name" in changed:
            ctx.log("settings.workspace_updated", "settings", "Workspace name updated")
    ctx.workspace = ws
    return settings_out(ctx)


def complete_onboarding(ctx: Context, body: S.OnboardingIn) -> None:
    u = ctx.user
    u.onboarding = {"goals": body.goals, "where": body.where}
    u.onboarding_completed_at = u.onboarding_completed_at or utcnow()
    if ctx.role in (WorkspaceRole.owner, WorkspaceRole.admin):
        ws = ctx.db.execute(select(Workspace).where(Workspace.id == ctx.workspace.id).with_for_update()).scalar_one()
        ws.ai_rules = body.ai_rules.model_dump(by_alias=True)
        ws.privacy_configured_at = ws.privacy_configured_at or utcnow()
        ctx.workspace = ws
    ctx.log(
        "settings.onboarding_completed",
        "settings",
        "Workspace setup completed",
        metadata={"goals": body.goals, "where": body.where},
    )


def account_request(ctx: Context, kind: str, reason: str | None) -> S.AccountRequestOut:
    r = AccountRequest(user_id=ctx.user.id, workspace_id=ctx.workspace.id, kind=kind, reason=reason)
    ctx.db.add(r)
    ctx.db.flush()
    if kind == "data_export":
        msg = "Request received. Exports are prepared by an administrator in Phase 2; you'll get an email when it's ready."
        ctx.log(
            "account.export_requested",
            "security",
            "Data export requested",
            target_type="account_request",
            target_id=str(r.id),
        )
    else:
        msg = "Request received. Deletion is reviewed manually; nothing is deleted automatically."
        ctx.log(
            "account.deletion_requested",
            "security",
            "Account deletion requested — manual review",
            target_type="account_request",
            target_id=str(r.id),
        )
    return S.AccountRequestOut(id=r.id, kind=kind, status="received", created_at=r.created_at, message=msg)
