"""Pydantic request/response schemas. Mirrors web/lib/api/types.ts (see API_CONTRACT.md)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.domain.enums import (
    ApprovalCategory,
    ApprovalDecisionKind,
    ApprovalStatus,
    ExecutionMode,
    ExecutionState,
    FileClassification,
    FileStatus,
    IntegrationStatus,
    PrivacyMode,
    RiskLevel,
    ScanStatus,
    StepRisk,
    StepState,
    TaskPriority,
    TaskStatus,
    TaskType,
    WaitingFor,
    WorkspaceRole,
)

T = TypeVar("T")


class In(BaseModel):
    """Base for request bodies: unknown fields are rejected, strings trimmed."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(Out, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int


class Message(Out):
    message: str


def _clean_name(v: str) -> str:
    from app.adapters.storage import strip_invisible

    v = strip_invisible(v).strip()
    if not v:
        raise ValueError("Enter a name.")
    return v


Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120), AfterValidator(_clean_name)
]
ShortCode = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
]

PASSWORD_MSG = "Use at least 10 characters, including a number."  # noqa: S105 - user-facing message


def _password(v: str) -> str:
    if len(v) < 10 or len(v) > 128 or not re.search(r"\d", v):
        raise ValueError(PASSWORD_MSG)
    return v


# ------------------------------------------------------------------ auth
class RegisterIn(In):
    name: Name
    email: EmailStr
    password: str = Field(max_length=128)
    accept_terms: bool

    _pw = field_validator("password")(classmethod(lambda cls, v: _password(v)))

    @field_validator("accept_terms")
    @classmethod
    def _terms(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Please accept the terms to continue.")
        return v


class LoginIn(In):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class EmailIn(In):
    email: EmailStr


class TokenIn(In):
    token: str = Field(min_length=10, max_length=100)


class ResetPasswordIn(In):
    token: str = Field(min_length=10, max_length=100)
    password: str = Field(max_length=128)

    _pw = field_validator("password")(classmethod(lambda cls, v: _password(v)))


class UserOut(Out):
    id: uuid.UUID
    name: str
    email: str
    email_verified: bool
    onboarding_completed: bool
    created_at: datetime


class WorkspaceSummaryOut(Out):
    id: uuid.UUID
    name: str
    role: WorkspaceRole


class MeOut(Out):
    user: UserOut
    workspace: WorkspaceSummaryOut
    demo: bool = False


class MfaRequiredOut(Out):
    """Password accepted; a second step is needed (the challenge travels in an httpOnly cookie)."""

    mfa_required: Literal[True] = True
    methods: list[str] = ["totp", "recovery_code"]


class MfaCodeIn(In):
    # A 6-digit authenticator code, or a recovery code like "abcd-efgh-ijkl".
    code: str = Field(min_length=6, max_length=32)


class MfaPasswordIn(In):
    password: str = Field(min_length=1, max_length=128)


class MfaPasswordCodeIn(In):
    password: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=6, max_length=32)


class MfaStatusOut(Out):
    enabled: bool
    pending_setup: bool
    enabled_at: datetime | None
    recovery_codes_remaining: int


class MfaSetupOut(Out):
    secret: str
    otpauth_uri: str
    qr_svg_data_uri: str


class RecoveryCodesOut(Out):
    recovery_codes: list[str]
    message: str


class CsrfOut(Out):
    csrf_token: str


class AiRules(In):
    share: bool = True
    publish: bool = True
    prod: bool = True
    # 'del' is a Python keyword; aliased on the wire.
    del_: bool = Field(default=True, alias="del")
    folders: bool = True
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class OnboardingIn(In):
    goals: list[ShortCode] = Field(default_factory=list, max_length=10)
    where: list[ShortCode] = Field(default_factory=list, max_length=10)
    ai_rules: AiRules


# ------------------------------------------------------------------ tasks
class StepPolicyOut(Out):
    outcome: Literal["allow", "require_approval", "deny"]
    reasons: list[str]
    data_leaves: bool
    classification: str
    version: str


class TaskStepOut(Out):
    id: uuid.UUID
    position: int
    title: str
    description: str
    tool: str
    risk: StepRisk
    requires_approval: bool
    state: StepState
    selected: bool
    started_at: datetime | None
    finished_at: datetime | None
    # Phase 3 (server-derived; optional so older clients keep working)
    step_key: str = ""
    tool_id: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)  # exact validated arguments the approval binds
    args_hash: str | None = None
    impact: str = "none"
    classifications: list[str] = Field(default_factory=list)
    destinations: list[str] = Field(default_factory=list)
    policy: StepPolicyOut | None = None
    output_summary: str | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    approval_id: uuid.UUID | None = None
    attempts: int = 0
    artifact_id: uuid.UUID | None = None


class TaskPlanOut(Out):
    steps: list[TaskStepOut]
    data_sources: list[str]
    destination: str | None
    version: int = 1
    summary: str | None = None
    provider: str | None = None
    provider_location: str | None = None


class TaskReportOut(Out):
    summary: str
    open_issues: str
    next_step: str


class TaskAttachmentOut(Out):
    file_id: uuid.UUID
    name: str


class TaskSummaryOut(Out):
    id: uuid.UUID
    ref: str
    title: str
    status: TaskStatus
    type: TaskType
    priority: TaskPriority
    mode: ExecutionMode
    integration_key: str | None
    waiting_for: WaitingFor | None
    created_at: datetime
    updated_at: datetime


class TaskArtifactOut(Out):
    id: uuid.UUID
    name: str
    kind: str
    provenance: Literal["ai_generated", "verified_result"]
    size_bytes: int
    created_at: datetime


class TaskOut(TaskSummaryOut):
    prompt: str
    question: str | None
    error_message: str | None
    attachments: list[TaskAttachmentOut]
    plan: TaskPlanOut | None
    report: TaskReportOut | None
    allowed_actions: list[str]
    data_classification: FileClassification = FileClassification.confidential
    started_at: datetime | None = None
    finished_at: datetime | None = None
    artifacts: list[TaskArtifactOut] = Field(default_factory=list)


class CreateTaskIn(In):
    title: str | None = Field(default=None, max_length=200)
    prompt: str = Field(max_length=4000)
    type: TaskType
    priority: TaskPriority = TaskPriority.normal
    mode: ExecutionMode = ExecutionMode.plan_only
    integration_key: str | None = Field(default=None, max_length=32)
    attachment_file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, v: str) -> str:
        if len(v.strip()) < 12:
            raise ValueError("Describe the task in a sentence or two so the Agent can plan it.")
        return v


class CancelIn(In):
    reason: str | None = Field(default=None, max_length=500)


class TaskMessageIn(In):
    text: str = Field(min_length=1, max_length=2000)
    kind: Literal["question", "answer", "plan_change"]


class ApprovePlanIn(In):
    selected_step_ids: list[uuid.UUID] = Field(max_length=50)


class RejectPlanIn(In):
    note: str | None = Field(default=None, max_length=1000)


class TaskEventOut(Out):
    id: uuid.UUID
    created_at: datetime
    level: Literal["info", "warn", "error", "note"]
    kind: str
    message: str


class ArtifactOut(Out):
    id: uuid.UUID
    task_id: uuid.UUID | None
    task_ref: str | None
    name: str
    kind: str
    provenance: Literal["ai_generated", "verified_result"]
    created_at: datetime


class ReportsOut(Out):
    artifacts: list[ArtifactOut]
    reports: list[TaskOut]


# ------------------------------------------------------------------ approvals
class ApprovalOut(Out):
    id: uuid.UUID
    ref: str
    category: ApprovalCategory
    title: str
    task_id: uuid.UUID | None
    task_ref: str | None
    reason: str
    affected_items: list[str]
    data_leaves_workspace: bool
    destination: str
    scope_summary: str
    consequences: str
    reversible: str
    risk: RiskLevel
    status: ApprovalStatus
    requested_at: datetime
    expires_at: datetime | None
    decided_at: datetime | None
    decision_note: str | None
    execution_state: ExecutionState
    # Phase 3 binding (shown to the approver)
    operation: str | None = None
    targets: list[str] = Field(default_factory=list)
    policy_version: str | None = None
    step_id: uuid.UUID | None = None


class ApprovalPage(Page[ApprovalOut]):
    pending_count: int


class DecisionIn(In):
    decision: ApprovalDecisionKind
    note: str | None = Field(default=None, max_length=1000)
    acknowledged: bool = False
    confirmed_items: list[str] | None = Field(default=None, max_length=500)


# ------------------------------------------------------------------ files
class FileOut(Out):
    id: uuid.UUID
    name: str
    type_label: str
    content_type: str
    size_bytes: int
    status: FileStatus
    classification: FileClassification
    collection: str
    uploaded_at: datetime
    error_message: str | None
    scan_status: ScanStatus
    deletion_requested: bool
    deleted_at: datetime | None = None
    restorable_until: datetime | None = None  # deleted files can be restored until this time


class CollectionCount(Out):
    name: str
    count: int


class FilePage(Page[FileOut]):
    collections: list[CollectionCount]


class UploadLimitsOut(Out):
    max_bytes: int
    allowed_types: list[str]
    collections: list[str]


class FilePatchIn(In):
    classification: FileClassification | None = None
    collection: str | None = Field(default=None, min_length=1, max_length=80)


class DeletionRequestIn(In):
    reason: str = Field(min_length=3, max_length=1000)


# ------------------------------------------------------------------ automations
class AutomationOut(Out):
    id: uuid.UUID
    ref: str
    name: str
    trigger: Literal["manual", "schedule", "event"]
    schedule_text: str
    timezone: str
    template: str
    tools: list[str]
    file_scope: str
    approval_policy: str
    last_run_text: str
    next_run_text: str
    enabled: bool
    created_at: datetime


TEMPLATES = ("WordPress audit (read-only)", "SEO report", "Plugin compatibility check", "Uptime and SSL check")
TOOLS = ("WordPress (read-only)", "WordPress (write)", "Browser runtime", "Agent analysis", "Email")
FILE_SCOPES = ("Private files only", "No files", "Approved files only")
POLICIES = ("Ask before any change", "Ask before publishing or sending", "Ask before external sharing only")
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class CreateAutomationIn(In):
    name: Name
    trigger: Literal["manual", "schedule"]
    frequency: Literal["daily", "weekly", "monthly"] | None = None
    day: str | None = None
    time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    template: str
    tools: list[str] = Field(default_factory=list, max_length=10)
    file_scope: str
    approval_policy: str

    @field_validator("template")
    @classmethod
    def _t(cls, v: str) -> str:
        if v not in TEMPLATES:
            raise ValueError("Choose one of the listed templates.")
        return v

    @field_validator("tools")
    @classmethod
    def _tools(cls, v: list[str]) -> list[str]:
        if any(t not in TOOLS for t in v):
            raise ValueError("Choose tools from the list.")
        return list(dict.fromkeys(v))

    @field_validator("file_scope")
    @classmethod
    def _fs(cls, v: str) -> str:
        if v not in FILE_SCOPES:
            raise ValueError("Choose one of the listed file scopes.")
        return v

    @field_validator("approval_policy")
    @classmethod
    def _ap(cls, v: str) -> str:
        if v not in POLICIES:
            raise ValueError("Choose one of the listed approval policies.")
        return v

    @field_validator("day")
    @classmethod
    def _day(cls, v: str | None) -> str | None:
        if v is not None and v not in DAYS:
            raise ValueError("Choose a day of the week.")
        return v


class AutomationPatchIn(In):
    enabled: bool


# ------------------------------------------------------------------ integrations, activity, notifications
class IntegrationOut(Out):
    key: str
    name: str
    icon: str
    status: IntegrationStatus
    description: str
    last_activity: str
    destination: str
    scopes: list[str]
    connectable: bool
    operational: bool = False  # a real, tested adapter exists (not a mock)
    mode: str | None = None
    site_url: str | None = None
    error_message: str | None = None
    write_access: bool = False


class ConnectWordPressIn(In):
    site_url: str = Field(min_length=10, max_length=300)
    # Optional write access (draft posts only): a WordPress Application Password for this user.
    username: str | None = Field(default=None, min_length=1, max_length=60)
    application_password: SecretStr | None = Field(default=None)

    @field_validator("application_password")
    @classmethod
    def _app_password(cls, v: SecretStr | None) -> SecretStr | None:
        if v is None:
            return None
        raw = v.get_secret_value().strip()
        if not 16 <= len(raw) <= 100 or any(ch in raw for ch in "\r\n\t"):
            raise ValueError("Paste the Application Password exactly as WordPress shows it.")
        return SecretStr(raw)

    @model_validator(mode="after")
    def _both_or_neither(self) -> ConnectWordPressIn:
        if (self.username is None) != (self.application_password is None):
            raise ValueError("Enter both the WordPress username and the Application Password, or neither.")
        return self

    @field_validator("site_url")
    @classmethod
    def _site(cls, v: str) -> str:
        from urllib.parse import urlsplit

        v = v.strip().rstrip("/")
        parts = urlsplit(v)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Enter the site's https:// address, like https://example.com")
        if not _DOMAIN.match(parts.hostname.lower()):
            raise ValueError("Enter a public domain name.")
        return f"https://{parts.hostname.lower()}{parts.path if parts.path not in ('', '/') else ''}"


ActivityType = Literal[
    "task", "plan", "approval", "execution", "error", "integration", "security", "auth", "file", "settings"
]


class ActivityOut(Out):
    id: uuid.UUID
    created_at: datetime
    type: ActivityType
    action: str
    message: str
    task_id: uuid.UUID | None
    task_ref: str | None
    actor: str | None


class NotificationOut(Out):
    id: uuid.UUID
    type: str
    icon: str
    text: str
    created_at: datetime
    unread: bool
    target: dict[str, Any] | None


class NotificationList(Out):
    items: list[NotificationOut]
    unread: int


# ------------------------------------------------------------------ dashboard, security, settings
class ProviderOut(Out):
    key: str
    label: str
    model: str
    location: Literal["local", "external"]
    configured: bool
    data_disclosure: str


class WorkerOut(Out):
    kind: str
    alive: bool
    last_heartbeat: datetime | None


class ToolOut(Out):
    id: str
    label: str
    description: str
    impact: str
    risk: StepRisk
    available: bool
    unavailable_reason: str | None


class AgentStatusOut(Out):
    status: Literal["not_configured", "not_connected", "ok", "degraded"]
    planner: Literal["not_configured", "not_connected", "ok", "degraded"]
    queue: Literal["not_configured", "not_connected", "ok", "degraded"]
    message: str
    provider: ProviderOut | None = None
    workers: list[WorkerOut] = Field(default_factory=list)
    queue_depth: dict[str, int] = Field(default_factory=dict)
    policy_version: str | None = None


class DashboardCounts(Out):
    in_progress: int
    awaiting_approval_plans: int
    pending_actions: int
    completed_week: int
    needs_attention: int


class SetupOut(Out):
    verify_email: bool
    privacy: bool
    integration: bool
    first_task: bool


class DashboardOut(Out):
    agent: AgentStatusOut
    counts: DashboardCounts
    setup: SetupOut
    recent_tasks: list[TaskSummaryOut]
    pending_approvals: list[ApprovalOut]
    recent_activity: list[ActivityOut]
    unread_notifications: int


class SecurityCheckOut(Out):
    key: str
    label: str
    status: Literal["enforced", "partial", "not_configured"]
    detail: str


class BlockedActionOut(Out):
    id: uuid.UUID
    created_at: datetime
    text: str
    reason: str


class LabelValue(Out):
    label: str
    value: str


class SecurityStatusOut(Out):
    privacy_mode: PrivacyMode
    privacy_enforced: bool
    checklist: list[SecurityCheckOut]
    blocked_actions: list[BlockedActionOut]
    recent_decisions: list[ApprovalOut]
    retention: list[LabelValue]
    ai_provider_policy: str


class NotificationPrefs(In):
    approval: bool = True
    done: bool = True
    failed: bool = True
    security: bool = True
    digest: bool = False


class ToolPermissions(In):
    wp_read: bool = True
    wp_write: bool = False
    browser: bool = True
    email: bool = False
    external: bool = False


class ProfileOut(Out):
    name: str
    email: str
    timezone: str


class WorkspaceSettingsOut(Out):
    name: str
    privacy_mode: PrivacyMode
    ai_rules: dict[str, bool]
    tool_permissions: dict[str, bool]
    allowed_domains: list[str]
    can_edit: bool


class SettingsOut(Out):
    profile: ProfileOut
    notifications: dict[str, bool]
    workspace: WorkspaceSettingsOut
    enforced: bool


# Domains under which anyone can host a site: allowlisting one would allow every site under it.
SHARED_SUFFIXES = frozenset(
    {
        "co.uk",
        "org.uk",
        "com.au",
        "co.in",
        "com.bd",
        "co.jp",
        "com.br",
        "co.nz",
        "co.za",
        "github.io",
        "gitlab.io",
        "herokuapp.com",
        "vercel.app",
        "netlify.app",
        "pages.dev",
        "web.app",
        "firebaseapp.com",
        "appspot.com",
        "blogspot.com",
        "wordpress.com",
        "wixsite.com",
        "squarespace.com",
        "cloudfront.net",
        "azurewebsites.net",
        "s3.amazonaws.com",
        "amazonaws.com",
        "onrender.com",
        "fly.dev",
        "workers.dev",
        "ngrok.io",
        "ngrok-free.app",
        "replit.app",
        "glitch.me",
    }
)

TIMEZONES = ("Asia/Dhaka", "UTC", "Europe/London", "America/New_York")
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class NotificationPrefsPatch(In):
    approval: bool | None = None
    done: bool | None = None
    failed: bool | None = None
    security: bool | None = None
    digest: bool | None = None


class PreferencesIn(In):
    name: Name | None = None
    timezone: str | None = None
    notifications: NotificationPrefsPatch | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is not None and v not in TIMEZONES:
            raise ValueError("Choose a timezone from the list.")
        return v


class AiRulesPatch(In):
    share: bool | None = None
    publish: bool | None = None
    prod: bool | None = None
    del_: bool | None = Field(default=None, alias="del")
    folders: bool | None = None
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ToolPermissionsPatch(In):
    wp_read: bool | None = None
    wp_write: bool | None = None
    browser: bool | None = None
    email: bool | None = None
    external: bool | None = None


class WorkspaceSettingsIn(In):
    name: Name | None = None
    privacy_mode: PrivacyMode | None = None
    ai_rules: AiRulesPatch | None = None
    tool_permissions: ToolPermissionsPatch | None = None
    allowed_domains: list[str] | None = Field(default=None, max_length=50)

    @field_validator("allowed_domains")
    @classmethod
    def _domains(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        out = []
        for d in v:
            d = d.strip().lower()
            if not d:
                continue
            if not _DOMAIN.match(d):
                raise ValueError(f"'{d[:60]}' is not a valid domain name.")
            if d in SHARED_SUFFIXES:
                raise ValueError(
                    f"'{d}' is shared by many unrelated sites. Add your own site's domain instead (e.g. mysite.{d})."
                )
            out.append(d)
        return list(dict.fromkeys(out))


class UsageItem(Out):
    key: str
    label: str
    used: int
    limit: int | None
    unit: str


class UsageOut(Out):
    period: str
    items: list[UsageItem]
    tracked: bool


class AccountDeletionIn(In):
    reason: str = Field(min_length=3, max_length=1000)


class AccountRequestOut(Out):
    id: uuid.UUID
    kind: Literal["data_export", "account_deletion"]
    status: Literal["received"]
    created_at: datetime
    message: str
