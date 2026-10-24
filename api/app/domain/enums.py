"""Wire enums shared by models, schemas and services. Values match web/lib/api/types.ts."""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    draft = "draft"
    queued = "queued"
    planning = "planning"
    awaiting_approval = "awaiting_approval"
    running = "running"
    needs_input = "needs_input"
    completed = "completed"
    failed = "failed"
    paused = "paused"
    cancelled = "cancelled"
    partially_completed = "partially_completed"


class TaskType(StrEnum):
    research = "research"
    website = "website"
    seo_content = "seo_content"
    code = "code"
    browser = "browser"
    file_analysis = "file_analysis"
    custom = "custom"


class TaskPriority(StrEnum):
    normal = "normal"
    high = "high"


class ExecutionMode(StrEnum):
    plan_only = "plan_only"
    prepare = "prepare"
    execute = "execute"


class WaitingFor(StrEnum):
    planning_service = "planning_service"
    execution_engine = "execution_engine"
    user_input = "user_input"
    approval = "approval"


class StepRisk(StrEnum):
    read_only = "read_only"
    workspace_only = "workspace_only"
    prepare_only = "prepare_only"
    production_change = "production_change"
    publish = "publish"
    external_transfer = "external_transfer"


class StepState(StrEnum):
    not_started = "not_started"
    in_progress = "in_progress"
    done = "done"
    failed = "failed"
    paused = "paused"
    waiting_for_input = "waiting_for_input"
    skipped = "skipped"
    cancelled = "cancelled"
    awaiting_approval = "awaiting_approval"  # waiting for its own one-time approval
    blocked = "blocked"  # denied by the policy engine; never runs
    rejected = "rejected"  # the user rejected its approval


class ApprovalCategory(StrEnum):
    external_transfer = "external_transfer"
    production_change = "production_change"
    publish = "publish"
    send_message = "send_message"
    record_deletion = "record_deletion"
    permission_change = "permission_change"
    code_execution = "code_execution"  # running a program in the sandbox


class ApprovalStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    changes_requested = "changes_requested"
    expired = "expired"


class ApprovalDecisionKind(StrEnum):
    approve = "approve"
    reject = "reject"
    request_changes = "request_changes"


class ExecutionState(StrEnum):
    not_executed = "not_executed"
    blocked_until_phase3 = "blocked_until_phase3"  # legacy (Phase 2 rows)
    pending_execution = "pending_execution"  # approved; the worker has not run it yet
    executed = "executed"
    execution_failed = "execution_failed"
    invalidated = "invalidated"  # the operation or policy changed after approval


class RiskLevel(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


class FileStatus(StrEnum):
    uploading = "uploading"
    unscanned = "unscanned"
    scanning = "scanning"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    quarantined = "quarantined"


class ScanStatus(StrEnum):
    not_configured = "not_configured"
    pending = "pending"
    clean = "clean"
    infected = "infected"


class FileClassification(StrEnum):
    """Data classification (Phase 3). Migration 0004 maps private→confidential and
    approved_integration→internal."""

    public = "public"
    internal = "internal"
    confidential = "confidential"
    unknown = "unknown"


DataClass = FileClassification


class WorkspaceRole(StrEnum):
    owner = "owner"
    admin = "admin"
    member = "member"
    viewer = "viewer"


class PrivacyMode(StrEnum):
    strict = "strict"
    approval = "approval"
    custom = "custom"


class IntegrationStatus(StrEnum):
    demo = "demo"
    not_connected = "not_connected"
    backend_required = "backend_required"
    connected = "connected"
    error = "error"


class AuthTokenPurpose(StrEnum):
    verify_email = "verify_email"
    reset_password = "reset_password"  # noqa: S105 - token purpose name, not a secret
    mfa_login = "mfa_login"  # second sign-in step after a correct password


class DeletionRequestStatus(StrEnum):
    pending_approval = "pending_approval"
    approved_blocked = "approved_blocked"  # legacy (Phase 2): approved while deletion was disabled
    approved = "approved"  # approved; waiting for the deletion service to re-validate and execute
    rejected = "rejected"
    changes_requested = "changes_requested"
    executed = "executed"  # soft-deleted by the deletion service (recoverable during retention)
    execution_refused = "execution_refused"  # re-validation failed right before execution
    restored = "restored"  # undone during the retention window


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"  # will not be retried; kept for inspection
    dead = "dead"  # dead-letter: retries exhausted or outcome unknown
    cancelled = "cancelled"


class JobQueue(StrEnum):
    orchestrator = "orchestrator"  # planning + task advancement (calls the model provider)
    tools = "tools"  # isolated tool execution
    deletion = "deletion"  # privileged deletion service


class ModelLocation(StrEnum):
    local = "local"  # runs on infrastructure you control; task data does not leave it
    external = "external"  # a third-party API receives the data you send


def values(e: type[StrEnum]) -> list[str]:
    return [m.value for m in e]
