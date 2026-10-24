"""Tool runner: executes one approved/allowed step in the tools worker.

Phase A (short transaction, row locks): re-check task status, step state, argument hash, policy
and — when required — the approval binding. Phase B (no locks held): run the tool with a timeout
and cooperative cancellation. Phase C (short transaction): store output/artifact, mark the step,
queue the orchestrator. Tool output is redacted and size-capped; it is data, never instructions.
"""

from __future__ import annotations

import concurrent.futures
import io
import json
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.storage import LocalPrivateFileStorage
from app.core.config import Settings
from app.core.db import utcnow
from app.domain.enums import ApprovalStatus, ExecutionState, JobQueue, StepState, TaskStatus
from app.engine import queue as Q
from app.engine.canonical import args_hash as compute_args_hash
from app.engine.env import build_env
from app.engine.orchestrator import RetryLater, current_steps
from app.engine.policy import POLICY_VERSION, evaluate_step
from app.engine.records import audit, event
from app.engine.tools import REGISTRY
from app.engine.tools.base import ToolContext, ToolError, ToolResult
from app.models import ApprovalRequest, Artifact, Task, TaskStep
from app.services.approvals import verify_binding

MAX_OUTPUT_BYTES = 64 * 1024
# Pause means: finish the step that already started, then start nothing new.
RUNNING_TASK_STATES = {TaskStatus.running, TaskStatus.paused}


def _cap_output(out: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(out, default=str)
    if len(raw) <= MAX_OUTPUT_BYTES:
        return json.loads(raw)  # type: ignore[no-any-return]
    return {"truncated": True, "note": "Output was too large and was truncated.", "head": raw[: MAX_OUTPUT_BYTES // 2]}


def _load(db: Session, job: Q.ClaimedJob, lock: bool) -> tuple[Task | None, TaskStep | None]:
    tq = select(Task).where(Task.workspace_id == job.workspace_id, Task.id == job.task_id)
    sq = select(TaskStep).where(TaskStep.workspace_id == job.workspace_id, TaskStep.id == job.step_id)
    if lock:
        tq, sq = tq.with_for_update(), sq.with_for_update()
    task = db.execute(tq).scalar_one_or_none()
    step = db.execute(sq).scalar_one_or_none() if task is not None else None
    return task, step


def _fail_step(
    db: Session, task: Task, step: TaskStep, job: Q.ClaimedJob, code: str, message: str, settings: Settings
) -> None:
    step.state = StepState.failed.value
    step.error = message[:1000]
    step.finished_at = utcnow()
    event(db, task, "step.failed", f"Step {step.step_key} failed: {message}", "error")
    audit(
        db,
        task.workspace_id,
        "step.failed",
        "execution",
        f"T-{task.number} step {step.step_key} failed ({code})",
        task_id=task.id,
        job_id=job.id,
        target_type="step",
        target_id=str(step.id),
        metadata={"code": code, "tool": step.tool_id},
    )
    Q.enqueue(
        db,
        workspace_id=task.workspace_id,
        kind="task.execute",
        queue=JobQueue.orchestrator,
        task_id=task.id,
        settings=settings,
        internal=True,
    )


def handle_tool_run(db: Session, job: Q.ClaimedJob, settings: Settings, factory: sessionmaker[Session]) -> None:
    # ---------------- phase A: verify under locks
    if job.locked_by and not Q.still_owned(db, job):
        return
    task, step = _load(db, job, lock=True)
    if task is None or step is None or step.plan_version != task.plan_version:
        return
    if TaskStatus(task.status) not in RUNNING_TASK_STATES:
        if step.state == StepState.in_progress.value and TaskStatus(task.status) == TaskStatus.cancelled:
            step.state = StepState.cancelled.value
        return
    if step.state != StepState.in_progress.value:
        return  # already finished or reset: duplicate delivery
    tool = REGISTRY.get(step.tool_id or "")
    if tool is None or tool.run is None:
        _fail_step(db, task, step, job, "tool_unknown", "This tool is not available.", settings)
        return
    try:
        args = tool.args_model.model_validate(step.args)
    except ValidationError:
        _fail_step(db, task, step, job, "invalid_args", "The stored arguments are no longer valid.", settings)
        return
    if compute_args_hash(tool.id, step.args) != step.args_hash:
        _fail_step(
            db,
            task,
            step,
            job,
            "args_changed",
            "The step's arguments changed after planning; refusing to run.",
            settings,
        )
        return
    env = build_env(db, task, settings)
    done = [s for s in current_steps(db, task) if s.state == StepState.done.value]
    for s in done:
        env.step_outputs_classification[s.step_key] = str((s.output or {}).get("_classification", "confidential"))
    # The approval covers the destinations shown when the step was planned. If settings changed since
    # (e.g. WordPress reconnected to another site), the step must not run against the new target.
    if tool.network and sorted(tool.destinations(args, env)) != sorted(step.destinations or []):
        _fail_step(
            db,
            task,
            step,
            job,
            "destination_changed",
            "The destination changed after this step was planned and approved; refusing to run.",
            settings,
        )
        return
    decision = evaluate_step(tool, args, env, settings, task.mode)
    if decision.outcome == "deny":
        step.state = StepState.blocked.value
        step.error = decision.reasons[0]
        event(
            db,
            task,
            "step.blocked",
            f"Step {step.step_key} blocked right before running: {decision.reasons[0]}",
            "warn",
        )
        audit(
            db,
            task.workspace_id,
            "policy.blocked",
            "security",
            f"T-{task.number} step {step.step_key} blocked: {decision.reasons[0]}",
            task_id=task.id,
            job_id=job.id,
            metadata={"reason": decision.reasons[0], "tool": tool.id, "rules": decision.rules},
        )
        Q.enqueue(
            db,
            workspace_id=task.workspace_id,
            kind="task.execute",
            queue=JobQueue.orchestrator,
            task_id=task.id,
            settings=settings,
            internal=True,
        )
        return
    external_ok = decision.outcome == "allow" and decision.data_leaves
    if decision.outcome == "require_approval":
        a = (
            db.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.workspace_id == task.workspace_id, ApprovalRequest.id == step.approval_id)
                .with_for_update()
            ).scalar_one_or_none()
            if step.approval_id
            else None
        )
        # A retry of the same approved step may reuse its (already consumed) approval.
        retry_of_same = (
            a is not None
            and a.status == ApprovalStatus.approved.value
            and a.execution_state == ExecutionState.executed.value
            and a.step_id == step.id
            and step.attempts > 0
        )
        reason = None
        if a is None:
            reason = "no approval on record"
        elif retry_of_same and not tool.idempotent:
            reason = "this action is not safe to repeat automatically"
        else:
            # Full re-verification, also for a retry (expiry, approver role, targets, policy, signature).
            reason = verify_binding(
                db,
                a,
                workspace_id=task.workspace_id,
                operation=tool.id,
                args_hash=step.args_hash,
                targets=list(step.destinations),
                step_id=step.id,
                policy_version=POLICY_VERSION,
                allow_consumed=retry_of_same,
            )
        if reason is not None:
            if a is not None and a.status == ApprovalStatus.approved.value:
                a.execution_state = ExecutionState.invalidated.value
            step.state = StepState.not_started.value  # the orchestrator will ask for a fresh approval
            event(db, task, "approval.invalidated", f"Step {step.step_key} was not run: {reason}.", "warn")
            audit(
                db,
                task.workspace_id,
                "approval.binding_failed",
                "security",
                f"T-{task.number} step {step.step_key}: {reason}",
                task_id=task.id,
                job_id=job.id,
                metadata={"reason": reason},
            )
            Q.enqueue(
                db,
                workspace_id=task.workspace_id,
                kind="task.execute",
                queue=JobQueue.orchestrator,
                task_id=task.id,
                settings=settings,
                internal=True,
            )
            return
        assert a is not None
        a.execution_state = ExecutionState.executed.value  # one-time: consumed before running
        external_ok = True
    step.attempts += 1
    step.started_at = step.started_at or utcnow()
    previous = {s.step_key: {**(s.output or {}), "_goal": s.title} for s in done}
    ctx_ids = (task.workspace_id, task.id, step.id, step.step_key, task.title, task.prompt, task.created_by)
    event(db, task, "step.started", f"Step {step.step_key} started: {step.title}")
    db.commit()

    # ---------------- phase B: run (no locks held)
    tool_db = factory()

    def is_cancelled() -> bool:
        with factory() as s:
            st = s.execute(
                select(Task.status).where(Task.workspace_id == ctx_ids[0], Task.id == ctx_ids[1])
            ).scalar_one()
            return st == TaskStatus.cancelled.value

    tctx = ToolContext(
        db=tool_db,
        settings=settings,
        workspace_id=ctx_ids[0],
        task_id=ctx_ids[1],
        step_id=ctx_ids[2],
        step_key=ctx_ids[3],
        task_title=ctx_ids[4],
        task_prompt=ctx_ids[5],
        requested_by=ctx_ids[6],
        env=env,
        previous_outputs=previous,
        is_cancelled=is_cancelled,
        external_transfer_authorised=external_ok,
    )
    result: ToolResult | None = None
    error: ToolError | None = None
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{tool.id}")
    fut = pool.submit(tool.run, tctx, args)
    try:
        result = fut.result(timeout=step.timeout_seconds + 5)
        tool_db.commit()
    except concurrent.futures.TimeoutError:
        error = ToolError("timeout", f"The tool did not finish within {step.timeout_seconds} s.", retryable=False)
    except ToolError as e:
        error = e
        tool_db.commit()  # keep usage records (e.g. model calls) for failed attempts
    except Exception as e:
        tool_db.rollback()
        error = ToolError("tool_crashed", f"The tool failed unexpectedly ({type(e).__name__}).", retryable=True)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        if fut.done():
            tool_db.close()

    # ---------------- phase C: record (only if this worker still owns the job)
    if job.locked_by and not Q.still_owned(db, job):
        db.rollback()
        return
    task, step = _load(db, job, lock=True)
    if task is None or step is None:
        return
    if TaskStatus(task.status) == TaskStatus.cancelled:
        step.state = StepState.cancelled.value
        step.finished_at = utcnow()
        event(
            db,
            task,
            "step.cancelled",
            f"Step {step.step_key} stopped because the task was cancelled; its result was discarded.",
            "warn",
        )
        return
    if error is not None:
        if error.retryable and tool.idempotent and job.attempts < job.max_attempts:
            event(
                db,
                task,
                "step.retrying",
                f"Step {step.step_key}: {error.message} — retrying (attempt {job.attempts} of {job.max_attempts}).",
                "warn",
            )
            db.commit()
            raise RetryLater(error.code)
        _fail_step(db, task, step, job, error.code, error.message, settings)
        return
    assert result is not None
    artifact_id: uuid.UUID | None = None
    if result.artifact is not None:
        storage = LocalPrivateFileStorage(settings.storage_dir)
        stored = storage.save(task.workspace_id, io.BytesIO(result.artifact.content), 5 * 1024 * 1024)
        art = Artifact(
            workspace_id=task.workspace_id,
            task_id=task.id,
            step_id=step.id,
            name=result.artifact.name[:200],
            kind=result.artifact.kind,
            provenance=result.artifact.provenance,
            storage_key=stored.key,
            content_type=result.artifact.content_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
        )
        db.add(art)
        db.flush()
        artifact_id = art.id
    out = _cap_output(result.output)
    out["_summary"] = result.summary[:500]
    out["_classification"] = result.classification
    if artifact_id:
        out["_artifact_id"] = str(artifact_id)
    step.output = out
    step.state = StepState.done.value
    step.error = None
    step.finished_at = utcnow()
    event(db, task, "step.done", f"Step {step.step_key} done: {result.summary}")
    audit(
        db,
        task.workspace_id,
        "step.executed",
        "execution",
        f"T-{task.number} step {step.step_key} ({tool.id}) done",
        task_id=task.id,
        job_id=job.id,
        target_type="step",
        target_id=str(step.id),
        metadata={"tool": tool.id, "attempt": job.attempts, "artifact": bool(artifact_id)},
    )
    Q.enqueue(
        db,
        workspace_id=task.workspace_id,
        kind="task.execute",
        queue=JobQueue.orchestrator,
        task_id=task.id,
        settings=settings,
        internal=True,
    )


def on_dead(db: Session, job: Q.ClaimedJob, error: str, settings: Settings) -> None:
    task, step = _load(db, job, lock=True)
    if task is None or step is None or step.state not in (StepState.in_progress.value,):
        return
    _fail_step(
        db,
        task,
        step,
        job,
        "job_dead",
        f"The step could not finish after {job.attempts} attempts ({error[:200]}).",
        settings,
    )
