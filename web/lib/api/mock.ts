// MockAgentApi — the isolated in-browser DEMO MODE (NEXT_PUBLIC_DEMO_MODE=true only).
// Ports the Phase 1 prototype's sample data and simulated behaviour onto the
// wire types. State lives in module memory and resets on reload. It never makes
// a network call, never reads file contents, and stores no credentials: the only
// thing persisted is a "signed in" boolean in sessionStorage.
import { ApiError, type AgentApi } from "./client";
import type {
  AccountRequest,
  ActivityEvent,
  AgentStatus,
  AgentTool,
  ActivityType,
  AiRules,
  Approval,
  ApprovalCategory,
  ApprovalStatus,
  Artifact,
  Automation,
  CreateAutomationInput,
  CreateTaskInput,
  Dashboard,
  DecisionInput,
  ExecutionMode,
  FileClassification,
  FileItem,
  FileListQuery,
  FileStatus,
  Integration,
  IntegrationStatus,
  LoginInput,
  LoginResult,
  Me,
  MfaSetup,
  MfaStatus,
  NotificationItem,
  OnboardingInput,
  Page,
  PreferencesInput,
  RecoveryCodes,
  RegisterInput,
  RiskLevel,
  SecurityStatus,
  Settings,
  StepRisk,
  StepState,
  Task,
  TaskAction,
  TaskAttachment,
  TaskEvent,
  TaskListQuery,
  TaskPlan,
  TaskPriority,
  TaskReport,
  TaskStatus,
  TaskSummary,
  TaskType,
  UploadLimits,
  Usage,
  User,
  WaitingFor,
  WordPressCredentials,
  WorkspaceSettingsInput,
  EventLevel,
} from "./types";
import { CLASSIFICATION, MODE_LABEL, PRIVACY_MODES } from "@/lib/labels";
import { emailOk, fmtDateTime, passwordOk, suggestTitle } from "@/lib/format";
import * as D from "./mock-data";

// ---------------------------------------------------------------- helpers

const SIGNED_IN_KEY = "steward.demo.signedIn";
const MIN = 60_000;
const HOUR = 3_600_000;
const DAY = 86_400_000;

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const latency = () => wait(150 + Math.random() * 150);
const authLatency = () => wait(900);
const clone = <T>(v: T): T => (typeof structuredClone === "function" ? structuredClone(v) : (JSON.parse(JSON.stringify(v)) as T));
const nowIso = () => new Date().toISOString();

/** Deterministic UUID-shaped id: kind (hex digit) + sequence number. */
function uid(kind: number, n: number): string {
  return `d3${kind.toString(16).padStart(6, "0")}-0000-4000-8000-${n.toString(16).padStart(12, "0")}`;
}
const K = { task: 1, approval: 2, file: 3, automation: 4, step: 5, activity: 6, notification: 7, artifact: 8, event: 9, blocked: 10, account: 11, request: 12, note: 13 };
let seqCounter = 1;
const nextId = (kind: number) => uid(kind, 100_000 + seqCounter++);

const MONTH_IDX: Record<string, number> = { Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5, Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11 };
/**
 * "Oct 24, 09:12" → ISO date anchored to today: the prototype's "today" is
 * Oct 24, so Oct 24 = today, Oct 23 = yesterday, … Times of day are kept (local).
 */
function anchor(stamp: string): string {
  const m = /^([A-Z][a-z]{2}) (\d{1,2}), (\d{2}):(\d{2})$/.exec(stamp);
  if (!m) return nowIso();
  const protoDay = Date.UTC(2026, 9, 24);
  const thatDay = Date.UTC(2026, MONTH_IDX[m[1]] ?? 9, Number(m[2]));
  const offset = Math.round((protoDay - thatDay) / DAY);
  const d = new Date();
  d.setHours(Number(m[3]), Number(m[4]), 0, 0);
  d.setDate(d.getDate() - offset);
  return d.toISOString();
}

/** "Expires in 23 h" / "Expires in 2 days" → ISO relative to now; "—" → null. */
function expiresFrom(text: string): string | null {
  const m = /Expires in (\d+) (h|day|days)/.exec(text);
  if (!m) return null;
  return new Date(Date.now() + Number(m[1]) * (m[2] === "h" ? HOUR : DAY)).toISOString();
}

function sizeToBytes(s: string): number {
  const m = /^([\d.]+)\s*(KB|MB|GB|B)$/.exec(s.trim());
  if (!m) return 0;
  const n = Number(m[1]);
  const mult = m[2] === "GB" ? 1024 ** 3 : m[2] === "MB" ? 1024 ** 2 : m[2] === "KB" ? 1024 : 1;
  return Math.round(n * mult);
}
function bytesLabel(n: number): string {
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function invalid(message: string, field?: string): ApiError {
  return new ApiError(422, "validation_error", message, null, field ? [{ field, message }] : null);
}
function notFound(): ApiError {
  return new ApiError(404, "not_found", "That item could not be found. It may have been removed.");
}
function conflict(code: string, message: string): ApiError {
  return new ApiError(409, code, message);
}
function paginate<T>(items: T[], page = 1, pageSize = 20): Page<T> {
  const size = Math.max(1, Math.min(100, pageSize));
  const pages = Math.max(1, Math.ceil(items.length / size));
  const p = Math.min(Math.max(1, page), pages);
  return { items: items.slice((p - 1) * size, p * size), page: p, page_size: size, total: items.length };
}
const startOfDay = (t: number) => {
  const d = new Date(t);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
};

// ---------------------------------------------------------------- label → enum maps

const STATUS: Record<string, TaskStatus> = {
  Draft: "draft",
  Queued: "queued",
  Planning: "planning",
  "Awaiting approval": "awaiting_approval",
  Running: "running",
  "Needs input": "needs_input",
  Completed: "completed",
  Failed: "failed",
  Paused: "paused",
  Cancelled: "cancelled",
  "Partially completed": "partially_completed",
};
const TYPE: Record<string, TaskType> = {
  Research: "research",
  "Website/WordPress": "website",
  "SEO/Content": "seo_content",
  Code: "code",
  "Browser workflow": "browser",
  "File analysis": "file_analysis",
  Custom: "custom",
};
const MODE: Record<string, ExecutionMode> = { "Plan only": "plan_only", "Prepare changes for review": "prepare", "Execute approved steps": "execute" };
const RISK_STEP: Record<string, StepRisk> = {
  "Read only": "read_only",
  "Workspace only": "workspace_only",
  "Prepare only": "prepare_only",
  "Production change": "production_change",
  Publish: "publish",
  "External transfer": "external_transfer",
};
const CATEGORY: Record<string, ApprovalCategory> = {
  "External data transfer": "external_transfer",
  "Production website change": "production_change",
  Publish: "publish",
  "Send message": "send_message",
  "Record deletion": "record_deletion",
  "Integration permission change": "permission_change",
};
const AP_STATUS: Record<string, ApprovalStatus> = {
  Pending: "pending",
  "Approved (simulated)": "approved",
  Rejected: "rejected",
  "Changes requested": "changes_requested",
};
const FILE_STATUS: Record<string, FileStatus> = {
  Uploading: "uploading",
  Scanning: "scanning",
  Processing: "processing",
  Ready: "ready",
  Failed: "failed",
  Quarantined: "quarantined",
};
const CLS: Record<string, FileClassification> = {
  Confidential: "confidential",
  Internal: "internal",
  Public: "public",
  Unknown: "unknown",
};
const INTEG_STATUS: Record<string, IntegrationStatus> = {
  Demo: "demo",
  "Not connected": "not_connected",
  "Backend required": "backend_required",
};
const ACT_TYPE: Record<string, ActivityType> = {
  Task: "task",
  Plan: "plan",
  Approval: "approval",
  Execution: "execution",
  Error: "error",
  Integration: "integration",
  Security: "security",
};
const CONTENT_TYPE: Record<string, string> = {
  CSV: "text/csv",
  PDF: "application/pdf",
  DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ZIP: "application/zip",
  JS: "text/javascript",
  MD: "text/markdown",
  TXT: "text/plain",
};

const kindOf = (t: TaskType): D.PlanKind =>
  t === "website" || t === "browser" ? "wp" : t === "file_analysis" || t === "code" ? "data" : "content";

// ---------------------------------------------------------------- internal records

interface NoteRec {
  id: string;
  at: string;
  text: string;
}
interface TaskRec {
  id: string;
  ref: string;
  num: number;
  title: string;
  status: TaskStatus;
  type: TaskType;
  priority: TaskPriority;
  mode: ExecutionMode;
  integration_key: string | null;
  created_at: string;
  updated_at: string;
  prompt: string;
  question: string | null;
  error_message: string | null;
  attachments: TaskAttachment[];
  report: TaskReport | null;
  /** per-step selection from plan approval; null = all selected */
  selected: boolean[] | null;
  /** true when cancelled before anything ran (plan rejected / cancelled while waiting) */
  neverRan: boolean;
  notes: NoteRec[];
}
interface ApprovalRec extends Approval {
  file_id: string | null; // set for file deletion requests
}

interface DemoState {
  signedIn: boolean;
  user: User;
  workspaceId: string;
  setup: { privacy: boolean; first_task: boolean };
  tasks: TaskRec[];
  taskSeq: number;
  approvals: ApprovalRec[];
  approvalSeq: number;
  files: FileItem[];
  automations: Automation[];
  integrations: Integration[];
  activity: ActivityEvent[];
  notifications: NotificationItem[];
  settings: Settings;
  idempotency: Map<string, string>;
  mfa: DemoMfa;
}

/**
 * Demo two-step sign-in, kept in this tab's memory only (resets on reload).
 * There is no real authenticator: the demo accepts the code 123456 (the UI says so)
 * or one of the recovery codes it generated. Each recovery code works once.
 */
interface DemoMfa {
  enabled: boolean;
  pendingSecret: string | null;
  enabledAt: string | null;
  recoveryCodes: string[]; // normalised (no dashes), unused ones only
  challengeUntil: number | null; // pending second sign-in step (like the server's 5-minute cookie)
}
const DEMO_TOTP = "123456";
const MFA_CHALLENGE_MS = 5 * MIN;

function randomChars(n: number, alphabet: string): string {
  const bytes = new Uint8Array(n);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) crypto.getRandomValues(bytes);
  else for (let i = 0; i < n; i++) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}
const normCode = (c: string) => c.replace(/[\s-]/g, "").toLowerCase();
const fmtRecovery = (c: string) => `${c.slice(0, 4)}-${c.slice(4, 8)}-${c.slice(8, 12)}`;

/** A clearly-labelled placeholder picture — not a scannable QR code. */
const DEMO_QR_SVG =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180" viewBox="0 0 180 180">' +
      '<rect width="180" height="180" fill="#fff"/><rect x="6" y="6" width="168" height="168" fill="none" stroke="#98A2B3" stroke-width="2" stroke-dasharray="6 5"/>' +
      '<text x="90" y="82" text-anchor="middle" font-family="monospace" font-size="18" fill="#475467">DEMO</text>' +
      '<text x="90" y="106" text-anchor="middle" font-family="monospace" font-size="11" fill="#667085">not a real QR code</text></svg>',
  );

function readSignedIn(): boolean {
  try {
    return typeof window !== "undefined" && window.sessionStorage.getItem(SIGNED_IN_KEY) === "true";
  } catch {
    return false;
  }
}
function writeSignedIn(on: boolean) {
  try {
    if (typeof window === "undefined") return;
    if (on) window.sessionStorage.setItem(SIGNED_IN_KEY, "true");
    else window.sessionStorage.removeItem(SIGNED_IN_KEY);
  } catch {
    /* storage unavailable — stays in memory only */
  }
}

function seed(): DemoState {
  const refToTaskId = (ref: string) => (ref ? uid(K.task, Number(ref.slice(2))) : null);
  const fileIdByName = new Map(D.FILES.map((f) => [f.name, uid(K.file, Number(f.id.slice(1)))]));

  const tasks: TaskRec[] = D.TASKS.map((t) => {
    const num = Number(t.id.slice(2));
    return {
      id: uid(K.task, num),
      ref: t.id,
      num,
      title: t.title,
      status: STATUS[t.status],
      type: TYPE[t.type],
      priority: t.priority === "High" ? "high" : "normal",
      mode: MODE[t.mode],
      integration_key: t.integ === "WordPress (demo)" ? "wordpress" : null,
      created_at: anchor(t.created),
      updated_at: anchor(t.updated),
      prompt: t.prompt,
      question: t.q ?? null,
      error_message: t.err ?? null,
      attachments: (t.attach ?? []).map((name) => ({ file_id: fileIdByName.get(name) ?? uid(K.file, 0), name })),
      report: t.report ? { summary: t.report.sum, open_issues: t.report.open, next_step: t.report.next } : null,
      selected: null,
      neverRan: false,
      notes: [],
    };
  });

  const approvals: ApprovalRec[] = D.APPROVALS.map((a) => {
    const status = AP_STATUS[a.status];
    return {
      id: uid(K.approval, Number(a.id.slice(2))),
      ref: a.id,
      category: CATEGORY[a.cat],
      title: a.title,
      task_id: refToTaskId(a.task),
      task_ref: a.task || null,
      reason: a.why,
      affected_items: [...a.affected],
      data_leaves_workspace: a.leaves,
      destination: a.dest,
      scope_summary: a.count,
      consequences: a.consequence,
      reversible: a.reversible,
      risk: a.risk.toLowerCase() as RiskLevel,
      status,
      requested_at: anchor(a.requested),
      expires_at: expiresFrom(a.expires),
      decided_at: a.decided ? anchor(a.decided) : null,
      decision_note: a.note ?? null,
      execution_state: status === "approved" ? "simulated" : "not_executed",
      file_id: null,
    };
  });

  const files: FileItem[] = D.FILES.map((f) => {
    const status = FILE_STATUS[f.status];
    return {
      id: uid(K.file, Number(f.id.slice(1))),
      name: f.name,
      type_label: f.type,
      content_type: CONTENT_TYPE[f.type] ?? "application/octet-stream",
      size_bytes: sizeToBytes(f.size),
      status,
      classification: CLS[f.cls],
      collection: f.coll,
      uploaded_at: anchor(f.up),
      error_message: f.err ?? null,
      scan_status: status === "quarantined" ? "infected" : status === "scanning" || status === "uploading" ? "pending" : "clean",
      deletion_requested: false,
    };
  });

  const automations: Automation[] = D.AUTOS.map((a) => ({
    id: uid(K.automation, Number(a.id.slice(3))),
    ref: a.id,
    name: a.name,
    trigger: a.trigger === "Manual" ? "manual" : "schedule",
    schedule_text: a.when,
    timezone: a.tz,
    template: a.template,
    tools: a.tools.split(", ").filter(Boolean),
    file_scope: a.cls,
    approval_policy: a.policy,
    last_run_text: a.last,
    next_run_text: a.next,
    enabled: a.status === "Enabled",
    created_at: anchor(a.created),
  }));

  const integrations: Integration[] = D.INTEGS.map((i) => ({
    key: i.id,
    name: i.name,
    icon: i.icon,
    status: INTEG_STATUS[i.status],
    description: i.scope,
    last_activity: i.last,
    destination: i.dest,
    scopes: [...i.scopes],
    connectable: i.id === "wordpress",
    operational: i.id === "wordpress",
    write_access: false,
    mode: i.id === "wordpress" ? "read-only (public REST API, no credentials) — demo sandbox" : "not built",
    site_url: null,
    error_message: null,
  }));

  const activity: ActivityEvent[] = D.ACT.map((a, i) => ({
    id: uid(K.activity, i + 1),
    created_at: anchor(a[0]),
    type: ACT_TYPE[a[1]],
    action: a[4],
    message: a[2],
    task_id: refToTaskId(a[3]),
    task_ref: a[3] || null,
    actor: "Demo User",
  }));

  const approvalIdByRef = (ref: string) => uid(K.approval, Number(ref.slice(2)));
  const notifications: NotificationItem[] = D.NOTES.map((n, i) => {
    const tid = n.target.ref && n.target.kind !== "approval" ? refToTaskId(n.target.ref)! : undefined;
    const target: NotificationItem["target"] =
      n.target.kind === "plan"
        ? { route: `/tasks/${tid}/plan`, id: tid }
        : n.target.kind === "task"
          ? { route: `/tasks/${tid}`, id: tid }
          : n.target.kind === "approval"
            ? { route: `/approvals?id=${approvalIdByRef(n.target.ref!)}`, id: approvalIdByRef(n.target.ref!) }
            : n.target.kind === "integrations"
              ? { route: "/integrations" }
              : { route: "/settings?tab=usage" };
    return { id: uid(K.notification, i + 1), type: n.type, icon: n.icon, text: n.text, created_at: anchor(n.stamp), unread: n.unread, target };
  });

  const user: User = {
    id: uid(K.account, 1),
    name: "Demo User",
    email: "demo@example.com",
    email_verified: false,
    onboarding_completed: false,
    created_at: nowIso(),
  };

  const settings: Settings = {
    profile: { name: user.name, email: user.email, timezone: "Asia/Dhaka" },
    notifications: { approval: true, done: true, failed: true, security: true, digest: false },
    workspace: {
      name: "Demo workspace",
      privacy_mode: "strict",
      ai_rules: { share: true, publish: true, prod: true, del: true, folders: true },
      tool_permissions: { wp_read: true, wp_write: false, browser: true, email: false, external: false },
      allowed_domains: ["example.com", "example.org"],
      can_edit: true,
    },
    enforced: false,
  };

  return {
    signedIn: readSignedIn(),
    user,
    workspaceId: uid(K.account, 2),
    setup: { privacy: false, first_task: false },
    tasks,
    taskSeq: 1049,
    approvals,
    approvalSeq: 312,
    files,
    automations,
    integrations,
    activity,
    notifications,
    settings,
    idempotency: new Map(),
    mfa: { enabled: false, pendingSecret: null, enabledAt: null, recoveryCodes: [], challengeUntil: null },
  };
}

let state: DemoState | null = null;
const db = (): DemoState => (state ??= seed());

// ---------------------------------------------------------------- task derivations

/** Ported from the prototype's stepStates(). */
function stepStates(t: TaskRec, n: number): StepState[] {
  const a: StepState[] = Array(n).fill("not_started");
  const done = (k: number) => {
    for (let i = 0; i < k && i < n; i++) a[i] = "done";
  };
  switch (t.status) {
    case "completed":
      done(n);
      break;
    case "running":
      done(2);
      a[2] = "in_progress";
      break;
    case "failed":
      done(2);
      a[2] = "failed";
      break;
    case "paused":
      done(2);
      a[2] = "paused";
      break;
    case "needs_input":
      done(1);
      a[1] = "waiting_for_input";
      break;
    case "partially_completed":
      done(3);
      for (let i = 3; i < n; i++) a[i] = "skipped";
      break;
    case "cancelled":
      if (!t.neverRan) done(1);
      for (let i = t.neverRan ? 0 : 1; i < n; i++) a[i] = "cancelled";
      break;
  }
  if (t.selected) t.selected.forEach((sel, i) => {
    if (!sel && a[i] !== "done") a[i] = "skipped";
  });
  return a;
}

function stepTime(t: TaskRec, i: number): number {
  return Date.parse(t.created_at) + (2 + i * 3) * MIN;
}

function buildPlan(t: TaskRec): TaskPlan | null {
  if (t.status === "draft" || t.status === "queued" || t.status === "planning") return null;
  const kd = kindOf(t.type);
  const raw = D.PLANS[kd];
  const states = stepStates(t, raw.length);
  return {
    steps: raw.map((p, i) => {
      const st = states[i];
      const at = stepTime(t, i);
      return {
        id: uid(K.step, t.num * 100 + i + 1),
        position: i + 1,
        title: p.t,
        description: p.d,
        tool: p.tool,
        risk: RISK_STEP[p.risk],
        requires_approval: !!p.approval,
        state: st,
        selected: t.selected ? t.selected[i] : true,
        started_at: st === "done" ? new Date(at - 2 * MIN).toISOString() : st === "in_progress" || st === "failed" || st === "paused" || st === "waiting_for_input" ? new Date(at).toISOString() : null,
        finished_at: st === "done" ? new Date(at).toISOString() : st === "failed" ? new Date(at + 30_000).toISOString() : null,
      };
    }),
    data_sources: D.KIND[kd].files.concat(t.attachments.map((a) => "Attached: " + a.name)),
    destination: D.KIND[kd].dest,
  };
}

const CANCELLABLE: TaskStatus[] = ["running", "paused", "queued", "needs_input", "awaiting_approval", "planning", "draft"];
function allowedActions(s: TaskStatus): TaskAction[] {
  const out: TaskAction[] = [];
  if (s === "running") out.push("pause");
  if (s === "paused") out.push("resume");
  if (CANCELLABLE.includes(s)) out.push("cancel");
  if (s === "needs_input") out.push("answer");
  if (s === "awaiting_approval") out.push("approve_plan", "reject_plan", "revise_plan");
  return out;
}
function waitingFor(s: TaskStatus): WaitingFor {
  if (s === "needs_input") return "user_input";
  if (s === "awaiting_approval") return "approval";
  return null;
}

function toSummary(t: TaskRec): TaskSummary {
  return {
    id: t.id,
    ref: t.ref,
    title: t.title,
    status: t.status,
    type: t.type,
    priority: t.priority,
    mode: t.mode,
    integration_key: t.integration_key,
    waiting_for: waitingFor(t.status),
    created_at: t.created_at,
    updated_at: t.updated_at,
  };
}
function toTask(t: TaskRec): Task {
  return clone({
    ...toSummary(t),
    prompt: t.prompt,
    question: t.question,
    error_message: t.error_message,
    attachments: t.attachments,
    plan: buildPlan(t),
    report: t.report,
    allowed_actions: allowedActions(t.status),
  });
}

/** Synthesised run log, ported from the prototype's `logs` + task notes. */
function taskEvents(t: TaskRec): TaskEvent[] {
  const out: TaskEvent[] = [];
  let n = 0;
  const push = (ms: number, level: EventLevel, kind: string, message: string) =>
    out.push({ id: uid(K.event, t.num * 1000 + ++n), created_at: new Date(ms).toISOString(), level, kind, message });
  const t0 = stepTime(t, -1);
  push(t0 + 4_000, "info", "task.created", "Task created by Demo User");
  push(t0 + 5_000, "info", "task.plan_requested", "Plan requested · mode: " + MODE_LABEL[t.mode]);
  push(t0 + 41_000, "info", "task.connection_check", "Workspace connection check · session=[REDACTED]");
  const raw = D.PLANS[kindOf(t.type)];
  stepStates(t, raw.length).forEach((st, i) => {
    const at = stepTime(t, i);
    const tool = raw[i].tool;
    if (st === "done") push(at + 12_000, "info", "task.step_finished", `Step ${i + 1} finished · ${tool}`);
    if (st === "in_progress") push(at + 3_000, "info", "task.step_started", `Step ${i + 1} started · ${tool} (demo simulation)`);
    if (st === "failed") push(at + 30_000, "error", "task.step_failed", `Step ${i + 1} failed · ${t.error_message || "Unexpected problem"}`);
    if (st === "waiting_for_input") push(at + 10_000, "warn", "task.waiting_for_input", `Waiting for input · ${t.question || ""}`);
  });
  t.notes.forEach((note) => out.push({ id: note.id, created_at: note.at, level: "note", kind: "task.message", message: note.text }));
  return out;
}

// ---------------------------------------------------------------- mutations shared by methods

function addActivity(type: ActivityType, action: string, message: string, task?: { id: string; ref: string } | null) {
  db().activity.unshift({
    id: nextId(K.activity),
    created_at: nowIso(),
    type,
    action,
    message,
    task_id: task?.id ?? null,
    task_ref: task?.ref ?? null,
    actor: "Demo User",
  });
}
function addNotification(icon: string, type: string, text: string, target: NotificationItem["target"]) {
  db().notifications.unshift({ id: nextId(K.notification), type, icon, text, created_at: nowIso(), unread: true, target });
}
function patchTask(t: TaskRec, patch: Partial<TaskRec>) {
  Object.assign(t, patch, { updated_at: nowIso() });
}
function addNote(t: TaskRec, text: string) {
  t.notes.push({ id: nextId(K.note), at: nowIso(), text });
}
function findTask(id: string): TaskRec {
  const t = db().tasks.find((x) => x.id === id || x.ref === id);
  if (!t) throw notFound();
  return t;
}
function findApproval(id: string): ApprovalRec {
  const a = db().approvals.find((x) => x.id === id || x.ref === id);
  if (!a) throw notFound();
  return a;
}
function findFile(id: string): FileItem {
  const f = db().files.find((x) => x.id === id);
  if (!f) throw notFound();
  return f;
}
function requireStatus(t: TaskRec, allowed: TaskStatus[], what: string) {
  if (!allowed.includes(t.status)) throw conflict("invalid_state", `This task can't be ${what} right now. Refresh to see its current status.`);
}
function toApproval(a: ApprovalRec): Approval {
  const { file_id: _ignored, ...rest } = a;
  void _ignored;
  return clone(rest);
}
function stampText(): string {
  return fmtDateTime(nowIso());
}

/** Prototype setTimeout: Planning → Awaiting approval after `ms`, with activity + notification. */
function finishPlanningLater(t: TaskRec, ms: number, notify: boolean) {
  setTimeout(() => {
    if (t.status !== "planning") return;
    patchTask(t, { status: "awaiting_approval" });
    if (notify) {
      addActivity("plan", "plan.prepared", `Plan prepared for ${t.ref} — waiting for your review`, t);
      addNotification("hourglass", "Approval requested", `Plan for ${t.ref} is ready for review`, { route: `/tasks/${t.id}/plan`, id: t.id });
    } else {
      addActivity("plan", "plan.revised", `Plan for ${t.ref} revised (demo: the sample plan stays the same)`, t);
    }
  }, ms);
}

function newTaskRec(fields: Pick<TaskRec, "title" | "prompt" | "status" | "type" | "priority" | "mode" | "integration_key" | "attachments">): TaskRec {
  const s = db();
  const num = s.taskSeq++;
  const now = nowIso();
  const t: TaskRec = {
    ...fields,
    id: uid(K.task, num),
    ref: `T-${num}`,
    num,
    created_at: now,
    updated_at: now,
    question: null,
    error_message: null,
    report: null,
    selected: null,
    neverRan: false,
    notes: [],
  };
  s.tasks.unshift(t);
  return t;
}

const sortTitle = (a: string, b: string) => a.localeCompare(b);

// ---------------------------------------------------------------- the API

function demoAgentStatus(): AgentStatus {
  return {
    status: "not_connected",
    planner: "not_connected",
    queue: "not_configured",
    message: "Demo mode — every result here is sample data. The demo agent does not touch any real system.",
    provider: {
      key: "demo",
      label: "Demo (sample data, no AI)",
      model: "none",
      location: "local",
      configured: false,
      data_disclosure: "Demo mode: nothing is sent anywhere. All plans and results are pre-written samples.",
    },
    workers: ["orchestrator", "tools", "deletion", "scheduler"].map((kind) => ({ kind, alive: false, last_heartbeat: null })),
    queue_depth: {},
    policy_version: null,
  };
}

const DEMO_TOOLS: AgentTool[] = [
  { id: "files.analyze", label: "Analyse an attached file", description: "Counts rows, columns, empty cells and frequent values.", impact: "none", risk: "read_only", available: true, unavailable_reason: null },
  { id: "seo.audit", label: "SEO audit of a page (read-only)", description: "Checks one page on an allowed domain.", impact: "external_read", risk: "read_only", available: true, unavailable_reason: null },
  { id: "report.write", label: "Write a report", description: "Writes a markdown report into the workspace.", impact: "workspace_write", risk: "workspace_only", available: true, unavailable_reason: null },
  { id: "wordpress.create_draft", label: "WordPress: create a draft post", description: "Creates a DRAFT post (never publishes, edits or deletes). Needs write access and a one-time approval of the exact text. Demo: nothing is sent.", impact: "production_change", risk: "production_change", available: true, unavailable_reason: null },
];

export class MockAgentApi implements AgentApi {
  readonly mode = "demo" as const;

  private requireAuth(): DemoState {
    const s = db();
    if (!s.signedIn) throw new ApiError(401, "not_authenticated", "Sign in to continue.");
    return s;
  }
  private me_(): Me {
    const s = db();
    return clone({ user: s.user, workspace: { id: s.workspaceId, name: s.settings.workspace.name, role: "owner" as const }, demo: true });
  }

  // ------------------------------------------------------------ auth
  async me(): Promise<Me> {
    await latency();
    this.requireAuth();
    return this.me_();
  }

  async register(input: RegisterInput): Promise<{ message: string }> {
    await authLatency();
    const details: { field: string; message: string }[] = [];
    if (!input.name?.trim()) details.push({ field: "name", message: "Enter your name." });
    if (!emailOk(input.email ?? "")) details.push({ field: "email", message: "Enter a valid email address, like name@example.com." });
    if (!passwordOk(input.password ?? "")) details.push({ field: "password", message: "Use at least 10 characters, including a number." });
    if (!input.accept_terms) details.push({ field: "accept_terms", message: "Please accept the terms to continue." });
    if (details.length) throw new ApiError(422, "validation_error", "Check the highlighted fields and try again.", null, details);
    const s = db();
    s.user = { ...s.user, name: input.name.trim(), email: input.email.trim(), email_verified: false, onboarding_completed: false };
    s.settings.profile = { ...s.settings.profile, name: s.user.name, email: s.user.email };
    // Demo keeps the prototype behaviour: registering signs you in to this tab.
    s.signedIn = true;
    writeSignedIn(true);
    addActivity("auth", "auth.registered", "Demo workspace created in this browser tab (nothing stored on a server)");
    return { message: "Demo workspace created in this browser tab. No email was sent — verify from the next screen to continue." };
  }

  async login(input: LoginInput): Promise<LoginResult> {
    await authLatency();
    if (!emailOk(input.email ?? "")) throw invalid("Enter a valid email address.", "email");
    if (!input.password) throw invalid("Enter your password.", "password");
    if (input.password === "error") {
      throw new ApiError(401, "invalid_credentials", "That email and password combination did not match. Check both and try again. (Demo error state.)");
    }
    const s = db();
    if (s.mfa.enabled) {
      // Same shape as the server: no session yet, only a short-lived challenge.
      s.signedIn = false;
      writeSignedIn(false);
      s.mfa.challengeUntil = Date.now() + MFA_CHALLENGE_MS;
      return { mfa_required: true, methods: ["totp", "recovery_code"] };
    }
    s.user = { ...s.user, email: input.email.trim() };
    s.settings.profile.email = s.user.email;
    s.signedIn = true;
    writeSignedIn(true);
    addActivity("auth", "auth.login", "Signed in to the demo workspace (not real authentication)");
    return this.me_();
  }

  async verifyMfa(code: string): Promise<Me> {
    await authLatency();
    const s = db();
    const m = s.mfa;
    if (!m.enabled || !m.challengeUntil || Date.now() > m.challengeUntil) {
      m.challengeUntil = null;
      throw new ApiError(401, "mfa_challenge_expired", "This sign-in attempt has expired. Enter your email and password again.");
    }
    if (!this.mfaCodeOk(code, true)) {
      throw new ApiError(401, "invalid_code", "That code didn't work. Check it and try again. (Demo: use 123456 or one of your recovery codes.)");
    }
    m.challengeUntil = null;
    s.signedIn = true;
    writeSignedIn(true);
    addActivity("auth", "auth.login", "Signed in to the demo workspace with two-step sign-in (not real authentication)");
    return this.me_();
  }

  /** Demo check: 123456 stands in for an authenticator code; a recovery code is used up. */
  private mfaCodeOk(code: string, allowRecovery: boolean): boolean {
    const c = normCode(code ?? "");
    if (c === DEMO_TOTP) return true;
    const m = db().mfa;
    const idx = allowRecovery ? m.recoveryCodes.indexOf(c) : -1;
    if (idx === -1) return false;
    m.recoveryCodes.splice(idx, 1);
    return true;
  }

  private demoPassword(password: string) {
    if (!password) throw invalid("Enter your password.", "password");
    if (password === "error") throw new ApiError(401, "invalid_password", "That password isn't right. (Demo: any password except \"error\" works.)");
  }

  private newRecoveryCodes(): string[] {
    const codes = Array.from({ length: 10 }, () => randomChars(12, "abcdefghijkmnpqrstuvwxyz23456789"));
    db().mfa.recoveryCodes = codes;
    return codes.map(fmtRecovery);
  }

  private mfaStatus_(): MfaStatus {
    const m = db().mfa;
    return { enabled: m.enabled, pending_setup: !m.enabled && !!m.pendingSecret, enabled_at: m.enabledAt, recovery_codes_remaining: m.enabled ? m.recoveryCodes.length : 0 };
  }

  async mfaStatus(): Promise<MfaStatus> {
    await latency();
    this.requireAuth();
    return this.mfaStatus_();
  }

  async setupMfa(password: string): Promise<MfaSetup> {
    await authLatency();
    const s = this.requireAuth();
    this.demoPassword(password);
    if (s.mfa.enabled) throw conflict("mfa_already_enabled", "Two-step sign-in is already on.");
    const secret = randomChars(32, "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567");
    s.mfa.pendingSecret = secret;
    return {
      secret,
      otpauth_uri: `otpauth://totp/Steward%20(demo):${encodeURIComponent(s.user.email)}?secret=${secret}&issuer=Steward%20(demo)`,
      qr_svg_data_uri: DEMO_QR_SVG,
    };
  }

  async enableMfa(code: string): Promise<RecoveryCodes> {
    await authLatency();
    const s = this.requireAuth();
    if (s.mfa.enabled || !s.mfa.pendingSecret) throw conflict("mfa_not_set_up", "Start the setup again: there is no pending two-step setup.");
    if (!this.mfaCodeOk(code, false)) throw new ApiError(400, "invalid_code", "That code didn't work. (Demo: enter 123456.)");
    s.mfa = { ...s.mfa, enabled: true, pendingSecret: null, enabledAt: nowIso() };
    const codes = this.newRecoveryCodes();
    addActivity("security", "auth.mfa_enabled", "Two-step sign-in turned on (demo — kept in this tab only)");
    return { recovery_codes: codes, message: "Two-step sign-in is on for this demo tab. Nothing was stored on a server." };
  }

  async disableMfa(password: string, code: string): Promise<MfaStatus> {
    await authLatency();
    const s = this.requireAuth();
    this.demoPassword(password);
    if (!s.mfa.enabled) throw conflict("mfa_not_enabled", "Two-step sign-in is already off.");
    if (!this.mfaCodeOk(code, true)) throw new ApiError(400, "invalid_code", "That code didn't work. (Demo: use 123456 or a recovery code.)");
    s.mfa = { enabled: false, pendingSecret: null, enabledAt: null, recoveryCodes: [], challengeUntil: null };
    addActivity("security", "auth.mfa_disabled", "Two-step sign-in turned off (demo)");
    return this.mfaStatus_();
  }

  async regenerateRecoveryCodes(password: string, code: string): Promise<RecoveryCodes> {
    await authLatency();
    const s = this.requireAuth();
    this.demoPassword(password);
    if (!s.mfa.enabled) throw conflict("mfa_not_enabled", "Turn on two-step sign-in first.");
    if (!this.mfaCodeOk(code, true)) throw new ApiError(400, "invalid_code", "That code didn't work. (Demo: use 123456 or a recovery code.)");
    const codes = this.newRecoveryCodes();
    addActivity("security", "auth.mfa_recovery_codes", "New recovery codes created; the old ones stopped working (demo)");
    return { recovery_codes: codes, message: "New recovery codes created. The old ones no longer work." };
  }

  async logout(): Promise<void> {
    await latency();
    const s = db();
    s.signedIn = false;
    writeSignedIn(false);
  }

  async verifyEmail(_token: string): Promise<{ message: string }> {
    void _token;
    await authLatency();
    const s = db();
    s.user.email_verified = true;
    if (s.signedIn) addActivity("auth", "auth.email_verified", "Email marked as verified (demo only)");
    return { message: "Email marked as verified (demo only — no email was sent)." };
  }

  async resendVerification(_email: string): Promise<{ message: string }> {
    void _email;
    await authLatency();
    return { message: "If that address needs verifying, a new link is on its way. (Demo: no email was sent.)" };
  }

  async forgotPassword(email: string): Promise<{ message: string }> {
    await authLatency();
    if (!emailOk(email ?? "")) throw invalid("Enter a valid email address.", "email");
    return { message: "If an account exists for that address, a reset link is on its way. (Demo: no email was sent.)" };
  }

  async resetPassword(_token: string, password: string): Promise<{ message: string }> {
    void _token;
    await authLatency();
    if (!passwordOk(password ?? "")) throw invalid("Use at least 10 characters, including a number.", "password");
    return { message: "Password updated (demo). Nothing was stored — sign in with any password." };
  }

  async completeOnboarding(input: OnboardingInput): Promise<Me> {
    await latency();
    const s = this.requireAuth();
    s.user.onboarding_completed = true;
    s.setup.privacy = true;
    s.settings.workspace.ai_rules = { ...input.ai_rules };
    addActivity("settings", "onboarding.completed", "Setup saved for this demo session" + (input.goals.length ? ` — goals: ${input.goals.join(", ")}` : ""));
    return this.me_();
  }

  // ------------------------------------------------------------ dashboard
  async dashboard(): Promise<Dashboard> {
    await latency();
    const s = this.requireAuth();
    const count = (list: TaskStatus[]) => s.tasks.filter((t) => list.includes(t.status)).length;
    const pending = s.approvals.filter((a) => a.status === "pending");
    const weekAgo = Date.now() - 7 * DAY;
    return clone({
      agent: demoAgentStatus(),
      counts: {
        in_progress: count(["queued", "planning", "running"]),
        awaiting_approval_plans: count(["awaiting_approval"]),
        pending_actions: pending.length,
        completed_week: s.tasks.filter((t) => t.status === "completed" && Date.parse(t.updated_at) >= weekAgo).length,
        needs_attention: count(["failed", "needs_input", "paused"]),
      },
      setup: { verify_email: s.user.email_verified, privacy: s.setup.privacy, integration: false, first_task: s.setup.first_task },
      recent_tasks: [...s.tasks].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 5).map(toSummary),
      pending_approvals: pending.slice(0, 3).map(toApproval),
      recent_activity: s.activity.slice(0, 6),
      unread_notifications: s.notifications.filter((n) => n.unread).length,
    });
  }

  async getAgentStatus(): Promise<AgentStatus> {
    await latency();
    this.requireAuth();
    return clone(demoAgentStatus());
  }

  async getAgentTools(): Promise<AgentTool[]> {
    await latency();
    this.requireAuth();
    return clone(DEMO_TOOLS);
  }

  // ------------------------------------------------------------ tasks
  async listTasks(query: TaskListQuery): Promise<Page<TaskSummary>> {
    await latency();
    const s = this.requireAuth();
    const q = (query.q ?? "").trim().toLowerCase();
    const today = startOfDay(Date.now());
    let list = s.tasks.filter((t) => {
      if (q && !`${t.title} ${t.ref} ${t.prompt}`.toLowerCase().includes(q)) return false;
      if (query.status && t.status !== query.status) return false;
      if (query.type && t.type !== query.type) return false;
      const created = Date.parse(t.created_at);
      if (query.created === "today" && created < today) return false;
      if (query.created === "3d" && created < today - 2 * DAY) return false;
      if (query.integration) {
        if (query.integration === "none" ? t.integration_key !== null : t.integration_key !== query.integration) return false;
      }
      return true;
    });
    const sort = query.sort ?? "updated";
    list = [...list].sort((a, b) =>
      sort === "title"
        ? sortTitle(a.title, b.title)
        : sort === "created"
          ? b.created_at.localeCompare(a.created_at) || b.num - a.num
          : b.updated_at.localeCompare(a.updated_at) || b.num - a.num,
    );
    return clone(paginate(list.map(toSummary), query.page, query.page_size ?? 20));
  }

  async getTask(id: string): Promise<Task> {
    await latency();
    this.requireAuth();
    return toTask(findTask(id));
  }

  async createTask(input: CreateTaskInput, idempotencyKey: string): Promise<Task> {
    await latency();
    const s = this.requireAuth();
    const existing = idempotencyKey ? s.idempotency.get(idempotencyKey) : undefined;
    if (existing) return toTask(findTask(existing));

    const prompt = (input.prompt ?? "").trim();
    if (prompt.length < 12) throw invalid("Describe the task in a sentence or two so the Agent can plan it.", "prompt");
    if (prompt.length > 4000) throw invalid("Keep the description under 4,000 characters.", "prompt");
    const attachments: TaskAttachment[] = (input.attachment_file_ids ?? []).map((fid) => {
      const f = s.files.find((x) => x.id === fid);
      if (!f) throw invalid("One of the attached files no longer exists. Remove it and try again.", "attachment_file_ids");
      if (f.status !== "ready") throw invalid(`“${f.name}” isn't ready yet. Only ready files can be attached.`, "attachment_file_ids");
      return { file_id: f.id, name: f.name };
    });
    const integration_key = input.integration_key ?? (input.type === "website" ? "wordpress" : null);
    const t = newTaskRec({
      title: input.title?.trim() || suggestTitle(prompt),
      prompt,
      status: "planning",
      type: input.type,
      priority: input.priority,
      mode: input.mode,
      integration_key,
      attachments,
    });
    if (idempotencyKey) s.idempotency.set(idempotencyKey, t.id);
    s.setup.first_task = true;
    addActivity("task", "task.created", `Task ${t.ref} created`, t);
    finishPlanningLater(t, 1600, true);
    return toTask(t);
  }

  async cancelTask(id: string, reason?: string): Promise<Task> {
    await latency();
    this.requireAuth();
    const t = findTask(id);
    requireStatus(t, CANCELLABLE, "cancelled");
    const neverRan = ["draft", "queued", "planning", "awaiting_approval"].includes(t.status);
    patchTask(t, { status: "cancelled", neverRan });
    if (reason?.trim()) addNote(t, "Cancelled: " + reason.trim());
    addActivity("task", "task.cancelled", `${t.ref} cancelled by Demo User`, t);
    return toTask(t);
  }

  async pauseTask(id: string): Promise<Task> {
    await latency();
    this.requireAuth();
    const t = findTask(id);
    requireStatus(t, ["running"], "paused");
    patchTask(t, { status: "paused" });
    addActivity("task", "task.paused", `${t.ref} paused by Demo User`, t);
    return toTask(t);
  }

  async resumeTask(id: string): Promise<Task> {
    await latency();
    this.requireAuth();
    const t = findTask(id);
    requireStatus(t, ["paused"], "resumed");
    patchTask(t, { status: "running" });
    addActivity("task", "task.resumed", `${t.ref} resumed by Demo User`, t);
    return toTask(t);
  }

  async postTaskMessage(id: string, text: string, kind: "question" | "answer" | "plan_change"): Promise<Task> {
    await latency();
    this.requireAuth();
    const t = findTask(id);
    const txt = (text ?? "").trim();
    if (!txt) throw invalid("Write a message first.", "text");
    if (kind === "question") {
      addNote(t, "Question from you: " + txt);
      patchTask(t, {});
      addActivity("task", "task.message", `Question added to ${t.ref} (the demo agent cannot answer)`, t);
    } else if (kind === "answer") {
      requireStatus(t, ["needs_input"], "answered");
      addNote(t, "Your answer: " + txt);
      patchTask(t, { status: "running", question: null });
      addActivity("task", "task.answered", `${t.ref} received your answer and resumed (simulated)`, t);
    } else {
      requireStatus(t, ["awaiting_approval"], "revised");
      addNote(t, "Plan change requested: " + txt);
      patchTask(t, { status: "planning" });
      addActivity("plan", "plan.change_requested", `Plan change requested for ${t.ref}`, t);
      finishPlanningLater(t, 1400, false);
    }
    return toTask(t);
  }

  async approvePlan(id: string, selectedStepIds: string[]): Promise<Task> {
    await latency();
    this.requireAuth();
    const t = findTask(id);
    requireStatus(t, ["awaiting_approval"], "approved");
    const plan = buildPlan(t);
    const steps = plan?.steps ?? [];
    const chosen = new Set(selectedStepIds);
    const unknown = selectedStepIds.filter((sid) => !steps.some((st) => st.id === sid));
    if (unknown.length) throw invalid("The plan changed since you opened it. Refresh and review it again.", "selected_step_ids");
    const selected = steps.map((st) => chosen.has(st.id));
    const n = selected.filter(Boolean).length;
    if (n === 0) throw invalid("Select at least one step to approve.", "selected_step_ids");
    patchTask(t, { status: "running", selected });
    addActivity("approval", "plan.approved", `Plan for ${t.ref} approved (${n} of ${steps.length} steps) — simulated`, t);
    return toTask(t);
  }

  async rejectPlan(id: string, note?: string): Promise<Task> {
    await latency();
    this.requireAuth();
    const t = findTask(id);
    requireStatus(t, ["awaiting_approval"], "rejected");
    const n = note?.trim();
    patchTask(t, { status: "cancelled", neverRan: true });
    if (n) addNote(t, "Plan rejected: " + n);
    addActivity("plan", "plan.rejected", `Plan for ${t.ref} rejected${n ? ": " + n : ""}`, t);
    return toTask(t);
  }

  async listTaskEvents(id: string, page = 1): Promise<Page<TaskEvent>> {
    await latency();
    this.requireAuth();
    return clone(paginate(taskEvents(findTask(id)), page, 50));
  }

  // ------------------------------------------------------------ approvals
  async listApprovals(status: "pending" | "decided" | "all", page = 1): Promise<Page<Approval> & { pending_count: number }> {
    await latency();
    const s = this.requireAuth();
    const list = s.approvals.filter((a) => status === "all" || (status === "pending" ? a.status === "pending" : a.status !== "pending"));
    return { ...paginate(list.map(toApproval), page, 20), pending_count: s.approvals.filter((a) => a.status === "pending").length };
  }

  async getApproval(id: string): Promise<Approval> {
    await latency();
    this.requireAuth();
    return toApproval(findApproval(id));
  }

  async decideApproval(id: string, input: DecisionInput): Promise<Approval> {
    await latency();
    const s = this.requireAuth();
    const a = findApproval(id);
    if (a.status !== "pending") throw conflict("already_decided", "This request was already decided. Refresh to see the decision.");
    const note = (input.note ?? "").trim();
    if (input.decision === "approve") {
      if (!input.acknowledged) throw invalid("Tick the box to confirm you understand what this approval allows.", "acknowledged");
      if (a.category === "record_deletion") {
        const confirmed = [...(input.confirmed_items ?? [])].sort();
        const affected = [...a.affected_items].sort();
        if (confirmed.length !== affected.length || confirmed.some((x, i) => x !== affected[i])) {
          throw invalid("Confirm every item listed for deletion. This approval covers only the exact items shown.", "confirmed_items");
        }
      }
    } else if (input.decision === "request_changes" && !note) {
      throw invalid("Describe the change you need first.", "note");
    }
    const status: ApprovalStatus = input.decision === "approve" ? "approved" : input.decision === "reject" ? "rejected" : "changes_requested";
    Object.assign(a, {
      status,
      decided_at: nowIso(),
      decision_note: note || null,
      execution_state: status === "approved" ? "simulated" : "not_executed",
    });
    const task = a.task_id ? s.tasks.find((t) => t.id === a.task_id) ?? null : null;
    if (status === "changes_requested" && task) addNote(task, `Change requested on ${a.ref}: ${note}`);
    if (status !== "approved" && a.file_id) {
      const f = s.files.find((x) => x.id === a.file_id);
      if (f) f.deletion_requested = false;
    }
    const verb = status === "approved" ? "approved (simulated)" : status === "rejected" ? "rejected" : "— changes requested";
    addActivity("approval", `approval.${status}`, `${a.ref} ${verb}: ${a.title}`, task ?? (a.task_ref ? { id: a.task_id!, ref: a.task_ref } : null));
    return toApproval(a);
  }

  // ------------------------------------------------------------ files
  async uploadLimits(): Promise<UploadLimits> {
    await latency();
    this.requireAuth();
    return { max_bytes: D.UPLOAD_MAX_BYTES, allowed_types: [...D.UPLOAD_TYPES], collections: [...D.COLLECTIONS] };
  }

  async listFiles(query: FileListQuery): Promise<Page<FileItem> & { collections: { name: string; count: number }[] }> {
    await latency();
    const s = this.requireAuth();
    const q = (query.q ?? "").trim().toLowerCase();
    if (query.deleted) return clone({ ...paginate([] as FileItem[], query.page, query.page_size ?? 50), collections: [] });
    const list = s.files.filter(
      (f) =>
        (!query.collection || query.collection === "All files" || f.collection === query.collection) &&
        (!query.classification || f.classification === query.classification) &&
        (!query.status || f.status === query.status) &&
        (!q || f.name.toLowerCase().includes(q)),
    );
    const names = [...D.COLLECTIONS, ...s.files.map((f) => f.collection).filter((c) => !D.COLLECTIONS.includes(c))];
    const collections = [...new Set(names)].map((name) => ({ name, count: s.files.filter((f) => f.collection === name).length }));
    return clone({ ...paginate(list, query.page, query.page_size ?? 50), collections });
  }

  async getFile(id: string): Promise<FileItem> {
    await latency();
    this.requireAuth();
    return clone(findFile(id));
  }

  /**
   * Simulated upload. Only the file's name, size and type metadata are used —
   * the contents are never read (same honesty as the prototype's addSample()).
   */
  async uploadFile(file: File, collection: string): Promise<FileItem> {
    await latency();
    const s = this.requireAuth();
    const name = file?.name || "sample-upload.pdf";
    const ext = (name.includes(".") ? name.split(".").pop()! : "").toUpperCase();
    if (!D.UPLOAD_TYPES.includes(ext)) {
      throw new ApiError(415, "unsupported_file_type", `This file type isn't supported. Use ${D.UPLOAD_TYPES.join(", ")}.`);
    }
    const size = typeof file?.size === "number" && file.size > 0 ? file.size : sizeToBytes("1.1 MB");
    if (size > D.UPLOAD_MAX_BYTES) throw new ApiError(413, "file_too_large", `This file is larger than ${bytesLabel(D.UPLOAD_MAX_BYTES)}.`);
    const coll = collection && collection !== "All files" ? collection : "Research";
    const f: FileItem = {
      id: nextId(K.file),
      name,
      type_label: ext,
      content_type: CONTENT_TYPE[ext] ?? (file?.type || "application/octet-stream"),
      size_bytes: size,
      status: "uploading",
      classification: "unknown",
      collection: coll,
      uploaded_at: nowIso(),
      error_message: null,
      scan_status: "pending",
      deletion_requested: false,
    };
    s.files.unshift(f);
    const step = (status: FileStatus, ms: number) =>
      setTimeout(() => {
        f.status = status;
        if (status === "processing" || status === "ready") f.scan_status = "clean";
      }, ms);
    step("scanning", 1200);
    step("processing", 2600);
    step("ready", 4000);
    addActivity("file", "file.uploaded", `${name} added to ${coll} (demo — the file's contents were not read)`);
    return clone(f);
  }

  async updateFile(id: string, patch: { classification?: FileClassification; collection?: string }): Promise<FileItem> {
    await latency();
    this.requireAuth();
    const f = findFile(id);
    if (patch.classification && patch.classification !== f.classification) {
      if (!(patch.classification in CLASSIFICATION)) throw invalid("Choose a valid classification.", "classification");
      f.classification = patch.classification;
      addActivity("security", "file.classification_changed", `Classification of ${f.name} changed to ${CLASSIFICATION[patch.classification].short} (demo)`);
    }
    if (patch.collection !== undefined && patch.collection.trim() && patch.collection !== f.collection) {
      f.collection = patch.collection.trim();
      addActivity("file", "file.moved", `${f.name} moved to ${f.collection} (demo)`);
    }
    return clone(f);
  }

  async requestFileDeletion(id: string, reason: string): Promise<Approval> {
    await latency();
    const s = this.requireAuth();
    const f = findFile(id);
    const open = s.approvals.find((a) => a.file_id === f.id && a.status === "pending");
    if (open) throw conflict("deletion_already_requested", `Deletion of this file is already waiting for approval (${open.ref}).`);
    const num = s.approvalSeq++;
    const ap: ApprovalRec = {
      id: uid(K.approval, num),
      ref: `A-${num}`,
      category: "record_deletion",
      title: "Delete 1 file: " + f.name,
      task_id: null,
      task_ref: null,
      reason: reason?.trim() || "Requested by Demo User from Files & Knowledge.",
      affected_items: [f.name],
      data_leaves_workspace: false,
      destination: "Workspace file storage",
      scope_summary: "1 file · " + bytesLabel(f.size_bytes),
      consequences: "Tasks that use this file will no longer be able to read it.",
      reversible: "Partly — kept for 30 days, then permanently removed.",
      risk: "high",
      status: "pending",
      requested_at: nowIso(),
      expires_at: new Date(Date.now() + 24 * HOUR).toISOString(),
      decided_at: null,
      decision_note: null,
      execution_state: "not_executed",
      file_id: f.id,
    };
    s.approvals.unshift(ap);
    f.deletion_requested = true;
    addActivity("approval", "approval.requested", `${ap.ref} requested: delete ${f.name}`);
    return toApproval(ap);
  }

  fileDownloadUrl(_id: string): string | null {
    void _id;
    return null; // demo files have no content
  }

  async restoreFile(id: string): Promise<FileItem> {
    await latency();
    this.requireAuth();
    void id;
    throw conflict("file_not_deleted", "Demo files are never deleted, so there is nothing to restore.");
  }

  artifactDownloadUrl(_id: string): string | null {
    void _id;
    return null; // demo reports have no stored file
  }

  // ------------------------------------------------------------ automations
  async listAutomations(): Promise<Automation[]> {
    await latency();
    return clone(this.requireAuth().automations);
  }

  async createAutomation(input: CreateAutomationInput): Promise<Automation> {
    await latency();
    const s = this.requireAuth();
    const name = (input.name ?? "").trim();
    if (!name) throw invalid("Give the automation a name.", "name");
    if (!input.template) throw invalid("Choose a template.", "template");
    const manual = input.trigger === "manual";
    const time = input.time || "08:00";
    if (!manual && !/^\d{2}:\d{2}$/.test(time)) throw invalid("Enter a time like 08:00.", "time");
    const freq = input.frequency ?? "weekly";
    const when = manual
      ? "When you start it"
      : freq === "weekly"
        ? `Every ${input.day || "Monday"} at ${time}`
        : freq === "daily"
          ? `Every day at ${time}`
          : `1st of each month at ${time}`;
    const a: Automation = {
      id: nextId(K.automation),
      ref: `AU-${s.automations.length + 1}`,
      name,
      trigger: manual ? "manual" : "schedule",
      schedule_text: when,
      timezone: s.settings.profile.timezone,
      template: input.template,
      tools: [...(input.tools ?? [])],
      file_scope: input.file_scope,
      approval_policy: input.approval_policy,
      last_run_text: "Never run",
      next_run_text: manual ? "Runs when you start it" : "Paused",
      enabled: manual, // scheduled automations are saved paused
      created_at: nowIso(),
    };
    s.automations.unshift(a);
    addActivity("task", "automation.created", `Automation "${a.name}" created (demo)`);
    return clone(a);
  }

  async setAutomationEnabled(id: string, enabled: boolean): Promise<Automation> {
    await latency();
    const s = this.requireAuth();
    const a = s.automations.find((x) => x.id === id || x.ref === id);
    if (!a) throw notFound();
    if (enabled && a.trigger !== "manual") {
      throw conflict(
        "scheduler_not_configured",
        "Scheduled automations stay paused until the scheduler is set up (with a job queue, back-pressure and duplicate-run protection). Your settings are saved.",
      );
    }
    if (a.enabled !== enabled) {
      a.enabled = enabled;
      addActivity("settings", enabled ? "automation.enabled" : "automation.paused", `Automation "${a.name}" ${enabled ? "enabled" : "paused"} (demo)`);
    }
    return clone(a);
  }

  async runAutomation(id: string): Promise<Task> {
    await latency();
    const s = this.requireAuth();
    const a = s.automations.find((x) => x.id === id || x.ref === id);
    if (!a) throw notFound();
    const t = newTaskRec({
      title: a.name,
      prompt: `Run automation "${a.name}" using template ${a.template}.`,
      status: "queued",
      type: a.template.includes("WordPress") || a.template.includes("Plugin") ? "website" : "seo_content",
      priority: "normal",
      mode: "plan_only",
      integration_key: "wordpress",
      attachments: [],
    });
    a.last_run_text = stampText() + " (demo)";
    addActivity("task", "automation.run", `Automation "${a.name}" queued ${t.ref} (demo)`, t);
    return toTask(t);
  }

  // ------------------------------------------------------------ integrations
  async listIntegrations(): Promise<Integration[]> {
    await latency();
    return clone(this.requireAuth().integrations);
  }

  async connectIntegration(key: string): Promise<Integration> {
    await latency();
    const s = this.requireAuth();
    const i = s.integrations.find((x) => x.key === key);
    if (!i) throw notFound();
    if (i.key === "wordpress" && i.status === "not_connected") {
      i.status = "demo";
      i.last_activity = "Demo sandbox — no real website";
      addActivity("integration", "integration.restored", "WordPress demo sandbox restored");
      return clone(i);
    }
    throw conflict(
      "integration_not_available",
      "Connecting needs the backend. Connections use a secure sign-in flow handled by the server, with least-privilege scopes and encrypted credential storage. The demo never asks for passwords or API keys.",
    );
  }

  async connectWordPress(siteUrl: string, credentials?: WordPressCredentials): Promise<Integration> {
    await latency();
    const s = this.requireAuth();
    const i = s.integrations.find((x) => x.key === "wordpress");
    if (!i) throw notFound();
    if (!/^https:\/\/[a-z0-9.-]+\.[a-z]{2,}(\/.*)?$/i.test(siteUrl.trim())) throw invalid("Enter the site's https:// address, like https://example.com", "site_url");
    // Same rules as the server. The password is checked and then dropped: the demo never keeps it.
    const user = credentials?.username?.trim() ?? "";
    const pw = credentials?.application_password?.trim() ?? "";
    if (!!user !== !!pw) throw invalid("Enter both the WordPress username and the Application Password, or neither.", user ? "application_password" : "username");
    if (pw && (pw.length < 16 || pw.length > 100 || /[\r\n\t]/.test(pw))) throw invalid("Paste the Application Password exactly as WordPress shows it.", "application_password");
    const write = !!pw;
    i.write_access = write;
    i.scopes = ["Read published pages and posts (public REST API)", ...(write ? ["Create draft posts (never publishes, edits or deletes; each draft needs your approval)"] : [])];
    i.mode = write ? "read + draft posts — demo sandbox (the password was not stored)" : "read-only (public REST API, no credentials) — demo sandbox";
    i.status = "demo";
    i.site_url = siteUrl.trim().replace(/\/$/, "");
    i.error_message = null;
    i.last_activity = "Demo sandbox — no request was made to " + i.site_url;
    addActivity("integration", "integration.connect_requested", `WordPress demo connection for ${i.site_url} (sample data, nothing contacted)`);
    return clone(i);
  }

  async disconnectIntegration(key: string): Promise<Integration> {
    await latency();
    const s = this.requireAuth();
    const i = s.integrations.find((x) => x.key === key);
    if (!i) throw notFound();
    if (i.status !== "demo" && i.status !== "connected") throw conflict("integration_not_connected", `${i.name} isn't connected, so there is nothing to disconnect.`);
    i.status = "not_connected";
    i.write_access = false;
    i.last_activity = "Disconnected " + stampText();
    addActivity("integration", "integration.disconnected", `${i.name} demo connection removed`);
    return clone(i);
  }

  // ------------------------------------------------------------ reports & activity
  async listReports(): Promise<{ artifacts: Artifact[]; reports: Task[] }> {
    await latency();
    const s = this.requireAuth();
    const reports = s.tasks.filter((t) => t.report).map(toTask);
    const artifacts: Artifact[] = [];
    s.tasks
      .filter((t) => t.status === "completed" || t.status === "partially_completed")
      .forEach((t) =>
        D.ARTS[kindOf(t.type)].forEach(([name, kind, label], i) =>
          artifacts.push({
            id: uid(K.artifact, t.num * 10 + i + 1),
            task_id: t.id,
            task_ref: t.ref,
            name,
            kind,
            provenance: label.startsWith("AI") ? "ai_generated" : "verified_result",
            created_at: t.updated_at,
          }),
        ),
      );
    return clone({ artifacts, reports });
  }

  async listActivity(type?: ActivityType, page = 1): Promise<Page<ActivityEvent>> {
    await latency();
    const s = this.requireAuth();
    return clone(paginate(type ? s.activity.filter((a) => a.type === type) : s.activity, page, 25));
  }

  // ------------------------------------------------------------ notifications
  async listNotifications(unreadOnly = false): Promise<{ items: NotificationItem[]; unread: number }> {
    await latency();
    const s = this.requireAuth();
    return clone({ items: unreadOnly ? s.notifications.filter((n) => n.unread) : s.notifications, unread: s.notifications.filter((n) => n.unread).length });
  }

  async markNotificationRead(id: string): Promise<void> {
    await latency();
    const n = this.requireAuth().notifications.find((x) => x.id === id);
    if (!n) throw notFound();
    n.unread = false;
  }

  async markAllNotificationsRead(): Promise<void> {
    await latency();
    this.requireAuth().notifications.forEach((n) => (n.unread = false));
  }

  // ------------------------------------------------------------ security & settings
  async securityStatus(): Promise<SecurityStatus> {
    await latency();
    const s = this.requireAuth();
    return clone({
      privacy_mode: s.settings.workspace.privacy_mode,
      privacy_enforced: false,
      checklist: D.CHECK.map(([key, label]) => ({
        key,
        label,
        status: "not_configured" as const,
        detail: "Handled by the backend — not set up in demo mode.",
      })),
      blocked_actions: D.BLOCKED.map((b, i) => ({ id: uid(K.blocked, i + 1), created_at: anchor(b.t), text: b.text, reason: b.reason })),
      recent_decisions: s.approvals.filter((a) => a.status !== "pending").slice(0, 4).map(toApproval),
      retention: D.RETENTION.map((r) => ({ ...r })),
      ai_provider_policy: D.AI_PROVIDER_POLICY,
    });
  }

  async getSettings(): Promise<Settings> {
    await latency();
    return clone(this.requireAuth().settings);
  }

  async updatePreferences(input: PreferencesInput): Promise<Settings> {
    await latency();
    const s = this.requireAuth();
    if (input.name !== undefined) {
      const name = input.name.trim();
      if (!name) throw invalid("Enter your name.", "name");
      s.settings.profile.name = name;
      s.user.name = name;
    }
    if (input.timezone !== undefined) s.settings.profile.timezone = input.timezone;
    if (input.notifications) s.settings.notifications = { ...s.settings.notifications, ...input.notifications };
    addActivity("settings", "preferences.updated", "Preferences saved for this browser session (demo)");
    return clone(s.settings);
  }

  async updateWorkspaceSettings(input: WorkspaceSettingsInput): Promise<Settings> {
    await latency();
    const s = this.requireAuth();
    const ws = s.settings.workspace;
    if (input.name !== undefined) {
      if (!input.name.trim()) throw invalid("Enter a workspace name.", "name");
      ws.name = input.name.trim();
    }
    if (input.allowed_domains !== undefined) {
      const domains = input.allowed_domains.map((d) => d.trim().toLowerCase()).filter(Boolean);
      const bad = domains.find((d) => !/^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(d));
      if (bad) throw invalid(`“${bad}” isn't a valid domain. Use a form like example.com.`, "allowed_domains");
      ws.allowed_domains = [...new Set(domains)];
    }
    if (input.ai_rules) ws.ai_rules = { ...ws.ai_rules, ...input.ai_rules } as AiRules;
    if (input.tool_permissions) ws.tool_permissions = { ...ws.tool_permissions, ...input.tool_permissions };
    if (input.privacy_mode !== undefined) {
      ws.privacy_mode = input.privacy_mode;
      s.setup.privacy = true;
      const label = PRIVACY_MODES.find((p) => p.value === input.privacy_mode)?.label ?? input.privacy_mode;
      addActivity("security", "privacy.mode_changed", `Privacy mode set to "${label}" (demo)`);
    } else {
      addActivity("settings", "workspace.updated", "Workspace settings saved for this browser session (demo)");
    }
    return clone(s.settings);
  }

  async usage(): Promise<Usage> {
    await latency();
    const s = this.requireAuth();
    const d = new Date();
    const storageMb = Math.round(s.files.reduce((n, f) => n + f.size_bytes, 0) / (1024 * 1024));
    return {
      period: d.toLocaleString("en-US", { month: "long", year: "numeric" }),
      tracked: false,
      items: [
        { key: "tasks", label: "Tasks this month (example)", used: s.tasks.length, limit: 100, unit: "tasks" },
        { key: "ai_usage", label: "AI usage (example)", used: 0, limit: null, unit: "tokens" },
        { key: "storage", label: "File storage (example)", used: storageMb, limit: 1024, unit: "MB" },
        { key: "automations", label: "Automations (example)", used: s.automations.length, limit: 10, unit: "automations" },
      ],
    };
  }

  async requestDataExport(): Promise<AccountRequest> {
    await latency();
    this.requireAuth();
    addActivity("security", "account.export_requested", "Data export requested (demo — nothing was sent)");
    return {
      id: nextId(K.request),
      kind: "data_export",
      status: "received",
      created_at: nowIso(),
      message: "Request received (demo). In the real workspace, the export is prepared on the server and a download link is emailed to you. No request was sent.",
    };
  }

  async requestAccountDeletion(_reason: string): Promise<AccountRequest> {
    void _reason;
    await latency();
    this.requireAuth();
    addActivity("security", "account.deletion_requested", "Account deletion requested (demo — nothing was deleted)");
    return {
      id: nextId(K.request),
      kind: "account_deletion",
      status: "received",
      created_at: nowIso(),
      message:
        "Request received (demo). In the real workspace, deletion is confirmed by email and carried out after a waiting period, following the retention policy. No request was sent and nothing was deleted.",
    };
  }
}
