import type {
  AccountRequest,
  ActivityEvent,
  ActivityType,
  AgentStatus,
  AgentTool,
  Approval,
  ApprovalStatus,
  Artifact,
  Automation,
  CreateAutomationInput,
  CreateTaskInput,
  Dashboard,
  DecisionInput,
  FileClassification,
  FileItem,
  FileListQuery,
  Integration,
  LoginInput,
  LoginResult,
  Me,
  MfaRequired,
  MfaSetup,
  MfaStatus,
  NotificationItem,
  OnboardingInput,
  Page,
  PreferencesInput,
  RecoveryCodes,
  RegisterInput,
  SecurityStatus,
  Settings,
  Task,
  TaskEvent,
  TaskListQuery,
  TaskSummary,
  UploadLimits,
  Usage,
  WordPressCredentials,
  WorkspaceSettingsInput,
} from "./types";

/**
 * The single interface every screen uses. Two implementations:
 *  - HttpAgentApi: the real FastAPI backend (default).
 *  - MockAgentApi: in-browser sample data, only when NEXT_PUBLIC_DEMO_MODE=true.
 * Errors are thrown as ApiError and must be shown to the user — never
 * swallowed or turned into a fake success.
 */
export interface AgentApi {
  readonly mode: "http" | "demo";

  // auth
  me(): Promise<Me>;
  register(input: RegisterInput): Promise<{ message: string }>;
  /** Returns `Me` (signed in) or `{mfa_required}` — then call verifyMfa with the second-step code. */
  login(input: LoginInput): Promise<LoginResult>;
  /** Second sign-in step: a 6-digit authenticator code or a recovery code. */
  verifyMfa(code: string): Promise<Me>;
  logout(): Promise<void>;
  verifyEmail(token: string): Promise<{ message: string }>;
  /** Generic response whether or not the address has an account. */
  resendVerification(email: string): Promise<{ message: string }>;
  forgotPassword(email: string): Promise<{ message: string }>;
  resetPassword(token: string, password: string): Promise<{ message: string }>;
  completeOnboarding(input: OnboardingInput): Promise<Me>;

  // two-step sign-in (own account)
  mfaStatus(): Promise<MfaStatus>;
  /** Re-checks the password; returns a new secret + QR code (shown once). */
  setupMfa(password: string): Promise<MfaSetup>;
  /** Confirms setup with a current code; returns 10 recovery codes (shown once). */
  enableMfa(code: string): Promise<RecoveryCodes>;
  disableMfa(password: string, code: string): Promise<MfaStatus>;
  /** Replaces all recovery codes; the old ones stop working. */
  regenerateRecoveryCodes(password: string, code: string): Promise<RecoveryCodes>;

  // dashboard & agent
  dashboard(): Promise<Dashboard>;
  getAgentStatus(): Promise<AgentStatus>;
  getAgentTools(): Promise<AgentTool[]>;

  // tasks
  listTasks(query: TaskListQuery): Promise<Page<TaskSummary>>;
  getTask(id: string): Promise<Task>;
  createTask(input: CreateTaskInput, idempotencyKey: string): Promise<Task>;
  cancelTask(id: string, reason?: string): Promise<Task>;
  pauseTask(id: string): Promise<Task>;
  resumeTask(id: string): Promise<Task>;
  postTaskMessage(id: string, text: string, kind: "question" | "answer" | "plan_change"): Promise<Task>;
  approvePlan(id: string, selectedStepIds: string[]): Promise<Task>;
  rejectPlan(id: string, note?: string): Promise<Task>;
  listTaskEvents(id: string, page?: number): Promise<Page<TaskEvent>>;

  // approvals
  listApprovals(status: "pending" | "decided" | "all", page?: number): Promise<Page<Approval> & { pending_count: number }>;
  getApproval(id: string): Promise<Approval>;
  decideApproval(id: string, input: DecisionInput): Promise<Approval>;

  // files
  uploadLimits(): Promise<UploadLimits>;
  listFiles(query: FileListQuery): Promise<Page<FileItem> & { collections: { name: string; count: number }[] }>;
  getFile(id: string): Promise<FileItem>;
  uploadFile(file: File, collection: string): Promise<FileItem>;
  updateFile(id: string, patch: { classification?: FileClassification; collection?: string }): Promise<FileItem>;
  requestFileDeletion(id: string, reason: string): Promise<Approval>;
  fileDownloadUrl(id: string): string | null; // null in demo mode
  /** Owner/admin: restore a deleted file during the retention window (queued to the deletion service). */
  restoreFile(id: string): Promise<FileItem>;
  artifactDownloadUrl(id: string): string | null; // null in demo mode

  // automations
  listAutomations(): Promise<Automation[]>;
  createAutomation(input: CreateAutomationInput): Promise<Automation>;
  setAutomationEnabled(id: string, enabled: boolean): Promise<Automation>;
  runAutomation(id: string): Promise<Task>;

  // integrations
  listIntegrations(): Promise<Integration[]>;
  connectIntegration(key: string): Promise<Integration>;
  /**
   * WordPress: read-only through the public REST API, or — with an Application Password
   * (both fields or neither) — also draft posts. Verified by a worker before "connected".
   */
  connectWordPress(siteUrl: string, credentials?: WordPressCredentials): Promise<Integration>;
  disconnectIntegration(key: string): Promise<Integration>;

  // reports & activity
  listReports(): Promise<{ artifacts: Artifact[]; reports: Task[] }>;
  listActivity(type?: ActivityType, page?: number): Promise<Page<ActivityEvent>>;

  // notifications
  listNotifications(unreadOnly?: boolean): Promise<{ items: NotificationItem[]; unread: number }>;
  markNotificationRead(id: string): Promise<void>;
  markAllNotificationsRead(): Promise<void>;

  // security & settings
  securityStatus(): Promise<SecurityStatus>;
  getSettings(): Promise<Settings>;
  updatePreferences(input: PreferencesInput): Promise<Settings>;
  updateWorkspaceSettings(input: WorkspaceSettingsInput): Promise<Settings>;
  usage(): Promise<Usage>;
  requestDataExport(): Promise<AccountRequest>;
  requestAccountDeletion(reason: string): Promise<AccountRequest>;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId: string | null = null,
    public readonly details: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** Field-level validation messages keyed by field name, when the server sent them. */
  fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    if (Array.isArray(this.details)) {
      for (const d of this.details as { field?: string; message?: string }[]) {
        if (d && d.field && d.message && !out[d.field]) out[d.field] = d.message;
      }
    }
    return out;
  }
}

/** True when login() needs the second step instead of returning a session. */
export function isMfaRequired(r: LoginResult): r is MfaRequired {
  return (r as MfaRequired).mfa_required === true;
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

/** A short, user-facing message for any thrown error, including the request ID for support. */
export function errorMessage(e: unknown): string {
  if (isApiError(e)) return e.requestId ? `${e.message} (Ref: ${e.requestId.slice(0, 8)})` : e.message;
  return "Something went wrong. Nothing was changed. Please try again.";
}

export type { ApprovalStatus };
