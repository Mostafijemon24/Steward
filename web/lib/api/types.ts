// API contract types. These mirror the FastAPI Pydantic schemas in
// api/app/schemas and are documented in API_CONTRACT.md. Wire values are
// snake_case enums; user-facing labels live in lib/labels.ts.

export type UUID = string;
export type ISODate = string;

// ---------- common ----------
export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    request_id: string;
    details?: Record<string, unknown> | { field: string; message: string }[] | null;
  };
}

// ---------- auth / me ----------
export type WorkspaceRole = "owner" | "admin" | "member" | "viewer";

export interface User {
  id: UUID;
  name: string;
  email: string;
  email_verified: boolean;
  onboarding_completed: boolean;
  created_at: ISODate;
}

export interface WorkspaceSummary {
  id: UUID;
  name: string;
  role: WorkspaceRole;
}

export interface Me {
  user: User;
  workspace: WorkspaceSummary;
  demo: boolean; // true only for the in-browser MockAgentApi
}

export interface RegisterInput {
  name: string;
  email: string;
  password: string;
  accept_terms: boolean;
}
export interface LoginInput {
  email: string;
  password: string;
}

// ---------- two-step sign-in (MFA) ----------
export type MfaMethod = "totp" | "recovery_code";
/** Password accepted, but a second step is needed. No session exists yet (the challenge is an httpOnly cookie). */
export interface MfaRequired {
  mfa_required: true;
  methods: MfaMethod[];
}
export type LoginResult = Me | MfaRequired;

export interface MfaStatus {
  enabled: boolean;
  pending_setup: boolean;
  enabled_at: ISODate | null;
  recovery_codes_remaining: number;
}
export interface MfaSetup {
  secret: string;
  otpauth_uri: string;
  qr_svg_data_uri: string; // data:image/svg+xml… usable directly as <img src>
}
export interface RecoveryCodes {
  recovery_codes: string[];
  message: string;
}

// ---------- tasks ----------
export type TaskStatus =
  | "draft"
  | "queued"
  | "planning"
  | "awaiting_approval"
  | "running"
  | "needs_input"
  | "completed"
  | "failed"
  | "paused"
  | "cancelled"
  | "partially_completed";

export type TaskType = "research" | "website" | "seo_content" | "code" | "browser" | "file_analysis" | "custom";
export type TaskPriority = "normal" | "high";
export type ExecutionMode = "plan_only" | "prepare" | "execute";
/** Why a task is not moving. `planning_service` / `execution_engine` mean the Phase 3 engine is not installed. */
export type WaitingFor = "planning_service" | "execution_engine" | "user_input" | "approval" | null;

export type StepRisk =
  | "read_only"
  | "workspace_only"
  | "prepare_only"
  | "production_change"
  | "publish"
  | "external_transfer";

export type StepState =
  | "not_started"
  | "in_progress"
  | "done"
  | "failed"
  | "paused"
  | "waiting_for_input"
  | "skipped"
  | "cancelled"
  | "awaiting_approval" // waiting for its own one-time approval
  | "blocked" // denied by the policy engine; never runs
  | "rejected"; // its approval was declined

export type StepImpact =
  | "none"
  | "workspace_write"
  | "external_read"
  | "external_transfer"
  | "publish"
  | "send_message"
  | "production_change"
  | "sensitive_change"
  | "delete"
  | "permission_change";

/** Policy engine decision for a step (server-derived, never from the model). */
export interface StepPolicy {
  outcome: "allow" | "require_approval" | "deny";
  reasons: string[];
  data_leaves: boolean;
  classification: string;
  version: string;
}

export interface TaskStep {
  id: UUID;
  position: number; // 1-based
  title: string;
  description: string;
  tool: string;
  risk: StepRisk;
  requires_approval: boolean;
  state: StepState;
  selected: boolean;
  started_at: ISODate | null;
  finished_at: ISODate | null;
  // Phase 3 (optional so demo data without them still type-checks)
  step_key?: string; // "s1"
  tool_id?: string | null;
  args?: Record<string, unknown>; // exact validated arguments (what an approval binds)
  args_hash?: string | null;
  impact?: StepImpact;
  classifications?: string[];
  destinations?: string[];
  policy?: StepPolicy | null;
  output_summary?: string | null;
  output?: Record<string, unknown> | null; // tool result (data, not instructions)
  error?: string | null;
  approval_id?: UUID | null;
  attempts?: number;
  artifact_id?: UUID | null;
}

export interface TaskPlan {
  steps: TaskStep[];
  data_sources: string[];
  destination: string | null;
  version?: number;
  summary?: string | null;
  provider?: string | null; // model that produced the plan
  provider_location?: "local" | "external" | null;
}

export interface TaskArtifact {
  id: UUID;
  name: string;
  kind: string;
  provenance: "ai_generated" | "verified_result";
  size_bytes: number;
  created_at: ISODate;
}

export interface TaskReport {
  summary: string;
  open_issues: string;
  next_step: string;
}

export interface TaskAttachment {
  file_id: UUID;
  name: string;
}

export interface TaskSummary {
  id: UUID;
  ref: string; // "T-1048"
  title: string;
  status: TaskStatus;
  type: TaskType;
  priority: TaskPriority;
  mode: ExecutionMode;
  integration_key: string | null;
  waiting_for: WaitingFor;
  created_at: ISODate;
  updated_at: ISODate;
}

export interface Task extends TaskSummary {
  prompt: string;
  question: string | null;
  error_message: string | null;
  attachments: TaskAttachment[];
  plan: TaskPlan | null; // null until the planner has produced a plan
  report: TaskReport | null;
  allowed_actions: TaskAction[]; // computed server-side from the state machine
  data_classification?: FileClassification;
  started_at?: ISODate | null;
  finished_at?: ISODate | null;
  artifacts?: TaskArtifact[];
}

export type TaskAction = "cancel" | "pause" | "resume" | "answer" | "approve_plan" | "reject_plan" | "revise_plan";

export interface CreateTaskInput {
  title?: string;
  prompt: string;
  type: TaskType;
  priority: TaskPriority;
  mode: ExecutionMode;
  integration_key?: string | null;
  attachment_file_ids?: UUID[];
}

export interface TaskListQuery {
  q?: string;
  status?: TaskStatus;
  type?: TaskType;
  created?: "today" | "3d";
  integration?: string; // integration key or "none"
  sort?: "updated" | "created" | "title";
  page?: number;
  page_size?: number;
}

export type EventLevel = "info" | "warn" | "error" | "note";
export interface TaskEvent {
  id: UUID;
  created_at: ISODate;
  level: EventLevel;
  kind: string; // e.g. task.created, task.status_changed, task.message
  message: string;
}

export interface Artifact {
  id: UUID;
  task_id: UUID | null;
  task_ref: string | null;
  name: string;
  kind: string;
  provenance: "ai_generated" | "verified_result";
  created_at: ISODate;
}

// ---------- approvals ----------
export type ApprovalCategory =
  | "external_transfer"
  | "production_change"
  | "publish"
  | "send_message"
  | "record_deletion"
  | "permission_change"
  | "code_execution";

export type ApprovalStatus = "pending" | "approved" | "rejected" | "changes_requested" | "expired";
export type RiskLevel = "high" | "medium" | "low";
export type ExecutionState =
  | "not_executed"
  | "blocked_until_phase3" // legacy Phase 2 rows
  | "simulated" // demo mode only
  | "pending_execution" // approved; the worker has not run it yet
  | "executed"
  | "execution_failed"
  | "invalidated"; // the action or policy changed after approval

export interface Approval {
  id: UUID;
  ref: string; // "A-311"
  category: ApprovalCategory;
  title: string;
  task_id: UUID | null;
  task_ref: string | null;
  reason: string;
  affected_items: string[];
  data_leaves_workspace: boolean;
  destination: string;
  scope_summary: string; // "3 files · 2.4 MB"
  consequences: string;
  reversible: string;
  risk: RiskLevel;
  status: ApprovalStatus;
  requested_at: ISODate;
  expires_at: ISODate | null;
  decided_at: ISODate | null;
  decision_note: string | null;
  execution_state: ExecutionState;
  // Phase 3 binding shown to the approver
  operation?: string | null; // tool id or workflow, e.g. "seo.audit", "deletion.execute"
  targets?: string[];
  policy_version?: string | null;
  step_id?: UUID | null;
}

export type ApprovalDecision = "approve" | "reject" | "request_changes";
export interface DecisionInput {
  decision: ApprovalDecision;
  note?: string;
  acknowledged: boolean; // user ticked "I understand this is …"
  confirmed_items?: string[]; // required for record_deletion: must equal affected_items
}

// ---------- files ----------
export type FileStatus = "uploading" | "unscanned" | "scanning" | "processing" | "ready" | "failed" | "quarantined";
export type FileClassification = "public" | "internal" | "confidential" | "unknown";

export interface FileItem {
  id: UUID;
  name: string;
  type_label: string; // "CSV", "PDF"
  content_type: string;
  size_bytes: number;
  status: FileStatus;
  classification: FileClassification;
  collection: string;
  uploaded_at: ISODate;
  error_message: string | null;
  scan_status: "not_configured" | "pending" | "clean" | "infected";
  deletion_requested: boolean;
  deleted_at?: ISODate | null;
  restorable_until?: ISODate | null; // deleted files can be restored until then
}

export interface FileListQuery {
  q?: string;
  collection?: string;
  classification?: FileClassification;
  status?: FileStatus;
  deleted?: boolean; // "Recently deleted"
  page?: number;
  page_size?: number;
}

export interface UploadLimits {
  max_bytes: number;
  allowed_types: string[]; // human labels
  collections: string[];
}

// ---------- automations ----------
export type AutomationTrigger = "manual" | "schedule" | "event";
export interface Automation {
  id: UUID;
  ref: string;
  name: string;
  trigger: AutomationTrigger;
  schedule_text: string;
  timezone: string;
  template: string;
  tools: string[];
  file_scope: string;
  approval_policy: string;
  last_run_text: string;
  next_run_text: string;
  enabled: boolean;
  created_at: ISODate;
}
export interface CreateAutomationInput {
  name: string;
  trigger: "manual" | "schedule";
  frequency?: "daily" | "weekly" | "monthly";
  day?: string;
  time?: string;
  template: string;
  tools: string[];
  file_scope: string;
  approval_policy: string;
}

// ---------- integrations ----------
export type IntegrationStatus = "demo" | "not_connected" | "backend_required" | "connected" | "error";
export interface Integration {
  key: string;
  name: string;
  icon: string;
  status: IntegrationStatus;
  description: string;
  last_activity: string;
  destination: string;
  scopes: string[];
  connectable: boolean; // only integrations with a real adapter
  operational?: boolean; // a real, tested adapter exists (not a mock)
  mode?: string | null;
  site_url?: string | null;
  error_message?: string | null;
  write_access: boolean; // WordPress: draft posts enabled with an Application Password
}

/** Optional WordPress Application Password: both fields or neither. Never returned by the server. */
export interface WordPressCredentials {
  username: string;
  application_password: string;
}

// ---------- activity / notifications ----------
export type ActivityType = "task" | "plan" | "approval" | "execution" | "error" | "integration" | "security" | "auth" | "file" | "settings";
export interface ActivityEvent {
  id: UUID;
  created_at: ISODate;
  type: ActivityType;
  action: string; // machine name, e.g. task.created
  message: string;
  task_id: UUID | null;
  task_ref: string | null;
  actor: string | null;
}

export interface NotificationItem {
  id: UUID;
  type: string; // label, e.g. "Approval requested"
  icon: string;
  text: string;
  created_at: ISODate;
  unread: boolean;
  target: { route: string; id?: UUID } | null;
}

// ---------- dashboard ----------
export type ComponentStatus = "not_configured" | "not_connected" | "ok" | "degraded";
export interface ModelProviderInfo {
  key: string;
  label: string;
  model: string;
  location: "local" | "external";
  configured: boolean;
  data_disclosure: string; // plain-language statement of what the provider receives
}

export interface WorkerInfo {
  kind: string;
  alive: boolean;
  last_heartbeat: ISODate | null;
}

export interface AgentStatus {
  status: ComponentStatus;
  planner: ComponentStatus;
  queue: ComponentStatus;
  message: string;
  provider?: ModelProviderInfo | null;
  workers?: WorkerInfo[];
  queue_depth?: Record<string, number>;
  policy_version?: string | null;
}

export interface AgentTool {
  id: string;
  label: string;
  description: string;
  impact: string;
  risk: StepRisk;
  available: boolean;
  unavailable_reason: string | null;
}

export interface Dashboard {
  agent: AgentStatus;
  counts: {
    in_progress: number;
    awaiting_approval_plans: number;
    pending_actions: number;
    completed_week: number;
    needs_attention: number;
  };
  setup: { verify_email: boolean; privacy: boolean; integration: boolean; first_task: boolean };
  recent_tasks: TaskSummary[];
  pending_approvals: Approval[];
  recent_activity: ActivityEvent[];
  unread_notifications: number;
}

// ---------- security ----------
export type PrivacyMode = "strict" | "approval" | "custom";
export interface SecurityCheck {
  key: string;
  label: string;
  status: "enforced" | "partial" | "not_configured";
  detail: string;
}
export interface BlockedAction {
  id: UUID;
  created_at: ISODate;
  text: string;
  reason: string;
}
export interface SecurityStatus {
  privacy_mode: PrivacyMode;
  privacy_enforced: boolean;
  checklist: SecurityCheck[];
  blocked_actions: BlockedAction[];
  recent_decisions: Approval[];
  retention: { label: string; value: string }[];
  ai_provider_policy: string;
}

// ---------- settings ----------
export interface NotificationPrefs {
  approval: boolean;
  done: boolean;
  failed: boolean;
  security: boolean;
  digest: boolean;
}
export interface AiRules {
  share: boolean;
  publish: boolean;
  prod: boolean;
  del: boolean;
  folders: boolean;
}
export interface ToolPermissions {
  wp_read: boolean;
  wp_write: boolean;
  browser: boolean;
  email: boolean;
  external: boolean;
}
export interface Settings {
  profile: { name: string; email: string; timezone: string };
  notifications: NotificationPrefs;
  workspace: {
    name: string;
    privacy_mode: PrivacyMode;
    ai_rules: AiRules;
    tool_permissions: ToolPermissions;
    allowed_domains: string[];
    can_edit: boolean;
  };
  enforced: boolean; // false until the Phase 3 policy engine enforces these
}
export interface PreferencesInput {
  name?: string;
  timezone?: string;
  notifications?: Partial<NotificationPrefs>;
}
export interface WorkspaceSettingsInput {
  name?: string;
  privacy_mode?: PrivacyMode;
  ai_rules?: Partial<AiRules>;
  tool_permissions?: Partial<ToolPermissions>;
  allowed_domains?: string[];
}
export interface OnboardingInput {
  goals: string[];
  where: string[];
  ai_rules: AiRules;
}

export interface Usage {
  period: string;
  items: { key: string; label: string; used: number; limit: number | null; unit: string }[];
  tracked: boolean;
}

export interface AccountRequest {
  id: UUID;
  kind: "data_export" | "account_deletion";
  status: "received";
  created_at: ISODate;
  message: string;
}
