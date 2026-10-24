"""API-side glue for the agent engine: reacting to approval decisions, dispatching jobs,
agent status, the tool catalog, file restore and the WordPress (read-only) connection.

The API process never runs tools or calls the model; it only records decisions and queues jobs.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, select

from app import schemas as S
from app.core.config import get_settings
from app.core.db import utcnow
from app.core.deps import Context
from app.core.errors import ApiError
from app.domain.enums import (
    ApprovalDecisionKind,
    JobQueue,
    StepState,
    TaskStatus,
    WaitingFor,
    WorkspaceRole,
)
from app.engine import queue as Q
from app.engine.egress import host_allowed
from app.engine.orchestrator import MODEL_PLAN_OP
from app.engine.policy import POLICY_VERSION
from app.engine.providers import get_provider
from app.engine.tools import REGISTRY, tool_enabled
from app.engine.tools.base import PlanEnv, ProviderView
from app.models import (
    ApprovalRequest,
    DeletionRequest,
    FileRecord,
    IntegrationConnection,
    Task,
    TaskStep,
    WorkerHeartbeat,
)
from app.services.auth import DEFAULT_AI_RULES, DEFAULT_TOOLS

TERMINAL = {TaskStatus.completed, TaskStatus.failed, TaskStatus.cancelled, TaskStatus.partially_completed}


def dispatch_plan(ctx: Context, task: Task, feedback: str | None = None, dedupe: str | None = None) -> None:
    Q.enqueue(
        ctx.db,
        workspace_id=task.workspace_id,
        kind="task.plan",
        queue=JobQueue.orchestrator,
        task_id=task.id,
        payload={"feedback": feedback} if feedback else {},
        dedupe_key=dedupe,
    )


def dispatch_execute(ctx: Context, task: Task) -> None:
    Q.enqueue(ctx.db, workspace_id=task.workspace_id, kind="task.execute", queue=JobQueue.orchestrator, task_id=task.id)


def on_approval_decided(
    ctx: Context, a: ApprovalRequest, kind: ApprovalDecisionKind, note: str | None, dr: DeletionRequest | None
) -> None:
    from app.services.tasks import add_event, transition

    approved = kind == ApprovalDecisionKind.approve
    if dr is not None:
        if approved and a.operation == "deletion.execute":
            Q.enqueue(
                ctx.db,
                workspace_id=ctx.workspace.id,
                kind="deletion.execute",
                queue=JobQueue.deletion,
                payload={"deletion_request_id": str(dr.id)},
                dedupe_key=f"delete:{dr.id}",
                max_attempts=3,
            )
        return
    if a.task_id is None:
        return
    task = ctx.db.execute(
        select(Task).where(Task.workspace_id == ctx.workspace.id, Task.id == a.task_id).with_for_update()
    ).scalar_one_or_none()
    if task is None or TaskStatus(task.status) in TERMINAL:
        return

    if a.operation == MODEL_PLAN_OP:
        if approved:
            add_event(
                ctx, task, "approval.granted", f"{ctx.user.name} approved sending the task to the AI model. Planning…"
            )
            dispatch_plan(ctx, task, dedupe=f"plan:{task.id}:{a.id}")
        else:
            transition(
                ctx, task, TaskStatus.cancelled, "Sending the task to the AI model was declined — nothing was sent"
            )
            Q.cancel_task_jobs(ctx.db, ctx.workspace.id, task.id)
        return

    if a.step_id is None:
        return
    step = ctx.db.execute(
        select(TaskStep).where(TaskStep.workspace_id == ctx.workspace.id, TaskStep.id == a.step_id).with_for_update()
    ).scalar_one_or_none()
    if step is None or step.state != StepState.awaiting_approval.value:
        return
    if approved:
        step.state = StepState.not_started.value
        add_event(ctx, task, "approval.granted", f"Step {step.step_key} approved by {ctx.user.name} (one-time).")
    else:
        step.state = StepState.rejected.value
        step.error = "Approval declined" + (f": {note}" if note else "")
        add_event(ctx, task, "approval.declined", f"Step {step.step_key} will not run: approval declined.", "warn")
    if TaskStatus(task.status) == TaskStatus.needs_input:
        transition(ctx, task, TaskStatus.queued, "Continuing after the approval decision", WaitingFor.execution_engine)
    dispatch_execute(ctx, task)


# ---------------------------------------------------------------- status & catalog
def provider_out() -> S.ProviderOut:
    info = get_provider().info
    return S.ProviderOut(
        key=info.key,
        label=info.label,
        model=info.model,
        location=info.location,
        configured=info.configured,
        data_disclosure=info.data_disclosure,
    )


def workers_out(ctx: Context) -> list[S.WorkerOut]:
    cutoff = utcnow() - timedelta(seconds=60)
    rows = (
        ctx.db.execute(
            select(WorkerHeartbeat.kind, func.max(WorkerHeartbeat.heartbeat_at))
            .where(WorkerHeartbeat.stopped_at.is_(None))
            .group_by(WorkerHeartbeat.kind)
        )
        .tuples()
        .all()
    )
    seen = dict(rows)
    return [
        S.WorkerOut(kind=k, alive=bool(seen.get(k) and seen[k] >= cutoff), last_heartbeat=seen.get(k))
        for k in ("orchestrator", "tools", "deletion", "scheduler")
    ]


def agent_status(ctx: Context) -> S.AgentStatusOut:
    prov = provider_out()
    workers = workers_out(ctx)
    alive = {w.kind for w in workers if w.alive}
    stats = Q.queue_stats(ctx.db, ctx.workspace.id)
    depth = {q: v.get("pending", 0) + v.get("running", 0) for q, v in stats.items()}
    core = {"orchestrator", "tools"}
    if not prov.configured:
        status, planner, msg = (
            "not_configured",
            "not_configured",
            "No AI model is configured, so tasks are saved and wait. Nothing is sent anywhere.",
        )
    elif not core <= alive:
        missing = ", ".join(sorted(core - alive))
        status, planner, msg = (
            "degraded",
            "ok",
            f"The AI model is configured but these workers are not running: {missing}. Tasks wait in the queue.",
        )
    else:
        status, planner, msg = (
            "ok",
            "ok",
            f"Agent ready. Planning with {prov.label}. Every step is checked by the policy engine before it runs.",
        )
    return S.AgentStatusOut(
        status=status,
        planner=planner,
        queue="ok" if core <= alive else "degraded",
        message=msg,
        provider=prov,
        workers=workers,
        queue_depth=depth,
        policy_version=POLICY_VERSION,
    )


def tools_out(ctx: Context) -> list[S.ToolOut]:
    settings = get_settings()
    perms = {**DEFAULT_TOOLS, **(ctx.workspace.tool_permissions or {})}
    info = get_provider(settings).info
    env = PlanEnv(
        workspace_id=ctx.workspace.id,
        task_classification="confidential",
        attached_files={},
        allowed_domains=list(ctx.workspace.allowed_domains or []),
        privacy_mode=ctx.workspace.privacy_mode,
        tool_permissions=perms,
        ai_rules={**DEFAULT_AI_RULES, **(ctx.workspace.ai_rules or {})},
        provider=ProviderView(info.configured, info.location, info.label),
        wordpress_site=None,
    )
    out = []
    for t in REGISTRY.values():
        entry = t.catalog_entry(settings, tool_enabled(t, perms))
        try:
            impact = t.impact(t.args_model.model_construct(), env)
        except Exception:
            impact = "none"
        out.append(
            S.ToolOut(
                id=t.id,
                label=t.label,
                description=t.description,
                impact=impact,
                risk=t.risk,
                available=bool(entry["available"]),
                unavailable_reason=entry["unavailable_reason"],
            )
        )
    return out


# ---------------------------------------------------------------- files
def restore_file(ctx: Context, file_id: uuid.UUID) -> S.FileOut:
    from app.services.views import files_out

    ctx.require_role(WorkspaceRole.owner, WorkspaceRole.admin)
    f = ctx.db.execute(
        select(FileRecord).where(FileRecord.workspace_id == ctx.workspace.id, FileRecord.id == file_id)
    ).scalar_one_or_none()
    if f is None:
        raise ctx.deny("file", file_id, "file")
    if f.deleted_at is None:
        raise ApiError(409, "file_not_deleted", "This file is not deleted.")
    if f.purge_after is None or f.purge_after <= utcnow():
        raise ApiError(410, "retention_ended", "The retention period has ended; this file was permanently removed.")
    Q.enqueue(
        ctx.db,
        workspace_id=ctx.workspace.id,
        kind="deletion.restore",
        queue=JobQueue.deletion,
        payload={"file_id": str(f.id), "requested_by": str(ctx.user.id)},
        dedupe_key=f"restore:{f.id}:{f.deleted_at:%Y%m%d%H%M%S}",
    )
    ctx.log(
        "file.restore_requested",
        "file",
        f"Restore requested for {f.original_name}",
        target_type="file",
        target_id=str(f.id),
    )
    return files_out(ctx.db, ctx.workspace.id, [f])[0]


# ---------------------------------------------------------------- integrations
def store_wordpress_credentials(ctx: Context, username: str, password: str) -> None:
    """Encrypt and store; the API role can write this table but cannot read secret_enc back."""
    import json

    from sqlalchemy import update
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core import crypto
    from app.engine.tools.wp_tools import secret_purpose
    from app.models import IntegrationSecret

    sealed = crypto.seal_for_worker(
        json.dumps({"username": username, "password": password}).encode(), secret_purpose(ctx.workspace.id)
    )
    # UPDATE then INSERT: an upsert would need SELECT on secret_enc, which this role deliberately lacks.
    values = {"secret_enc": sealed, "created_by": ctx.user.id, "updated_at": utcnow(), "revoked_at": None}
    where = (IntegrationSecret.workspace_id == ctx.workspace.id, IntegrationSecret.key == "wordpress")
    res = ctx.db.execute(
        update(IntegrationSecret).where(*where).values(**values).execution_options(synchronize_session=False)
    )
    if getattr(res, "rowcount", 0) == 0:
        ins = ctx.db.execute(
            pg_insert(IntegrationSecret)
            .values(workspace_id=ctx.workspace.id, key="wordpress", secret_enc=sealed, created_by=ctx.user.id)
            .on_conflict_do_nothing()
        )
        if getattr(ins, "rowcount", 0) == 0:  # another request inserted it meanwhile: overwrite
            ctx.db.execute(
                update(IntegrationSecret).where(*where).values(**values).execution_options(synchronize_session=False)
            )


def revoke_integration_secret(ctx: Context, key: str) -> None:
    from sqlalchemy import update

    from app.models import IntegrationSecret

    ctx.db.execute(
        update(IntegrationSecret)
        .where(
            IntegrationSecret.workspace_id == ctx.workspace.id,
            IntegrationSecret.key == key,
            IntegrationSecret.revoked_at.is_(None),
        )
        .values(secret_enc=None, revoked_at=utcnow(), updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )


def connect_wordpress(ctx: Context, body: S.ConnectWordPressIn) -> None:
    ctx.require_role(WorkspaceRole.owner, WorkspaceRole.admin)
    host = urlsplit(body.site_url).hostname or ""
    if not host_allowed(host, ctx.workspace.allowed_domains or []):
        raise ApiError(
            422,
            "domain_not_allowed",
            f"Add {host} to Settings → Allowed domains first. The agent only contacts domains on that list.",
            details=[{"field": "site_url", "message": "Domain is not on the allowed list."}],
        )
    conn = ctx.db.execute(
        select(IntegrationConnection)
        .where(IntegrationConnection.workspace_id == ctx.workspace.id, IntegrationConnection.key == "wordpress")
        .with_for_update()
    ).scalar_one_or_none()
    if conn is None:
        conn = IntegrationConnection(workspace_id=ctx.workspace.id, key="wordpress", status="not_connected")
        ctx.db.add(conn)
    write = body.application_password is not None and body.username is not None
    conn.config = {
        "site_url": body.site_url,
        "mode": "read_write_drafts" if write else "read_only_public_rest",
        **({"username": body.username} if write else {}),
    }
    conn.granted_scopes = ["Read published pages and posts (public REST API)"] + (
        ["Create draft posts (never publishes, edits or deletes; each draft needs your approval)"] if write else []
    )
    conn.status = "not_connected"  # becomes connected only after the tools worker verifies it
    conn.error_message = "Checking the site…"
    conn.updated_at = utcnow()
    ctx.db.flush()
    if write:
        assert body.username is not None and body.application_password is not None
        store_wordpress_credentials(ctx, body.username, body.application_password.get_secret_value())
    else:
        revoke_integration_secret(ctx, "wordpress")
    Q.enqueue(
        ctx.db,
        workspace_id=ctx.workspace.id,
        kind="integration.verify",
        queue=JobQueue.tools,
        payload={"key": "wordpress"},
        dedupe_key=f"verify:wordpress:{body.site_url}:{utcnow():%Y%m%d%H%M}",  # at most one check per site per minute
        max_attempts=2,
    )
    ctx.log(
        "integration.connect_requested",
        "integration",
        f"WordPress connection requested for {host} "
        + ("(read + draft posts; Application Password stored encrypted)" if write else "(read-only, no credentials)"),
        target_type="integration",
        target_id="wordpress",
    )
