import { ApiError, type AgentApi } from "./client";
import type * as T from "./types";

const BASE = "/api/v1";
// Production uses the __Host- prefixed name (secure contexts only); development the plain one.
const CSRF_COOKIES = ["__Host-stw_csrf", "stw_csrf"];
const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
  return m ? decodeURIComponent(m.slice(name.length + 1)) : null;
}

function readCsrfCookie(): string | null {
  for (const name of CSRF_COOKIES) {
    const v = readCookie(name);
    if (v) return v;
  }
  return null;
}

function qs(params: object): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

/** Real backend adapter. Session lives in an HttpOnly cookie; nothing secret is kept in JS. */
export class HttpAgentApi implements AgentApi {
  readonly mode = "http" as const;
  private csrfPromise: Promise<string> | null = null;

  private async csrf(): Promise<string> {
    const existing = readCsrfCookie();
    if (existing) return existing;
    if (!this.csrfPromise) {
      this.csrfPromise = fetch(`${BASE}/auth/csrf`, { credentials: "same-origin" })
        .then(async (r) => {
          if (!r.ok) throw await toError(r);
          const body = (await r.json()) as { csrf_token: string };
          return readCsrfCookie() ?? body.csrf_token;
        })
        .finally(() => {
          this.csrfPromise = null;
        });
    }
    return this.csrfPromise;
  }

  private async req<R>(method: string, path: string, body?: unknown, extraHeaders?: Record<string, string>): Promise<R> {
    const headers: Record<string, string> = { Accept: "application/json", ...(extraHeaders ?? {}) };
    let payload: BodyInit | undefined;
    if (body instanceof FormData) payload = body;
    else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    if (UNSAFE.has(method)) headers["X-CSRF-Token"] = await this.csrf();

    let res: Response;
    try {
      res = await fetch(BASE + path, { method, headers, body: payload, credentials: "same-origin", cache: "no-store" });
    } catch {
      throw new ApiError(0, "network_error", "Could not reach the server. Check your connection and try again. Nothing was changed.");
    }
    if (res.status === 403) {
      // A stale CSRF cookie: fetch a fresh token once and retry.
      const err = await toError(res);
      if (err.code === "csrf_failed" && UNSAFE.has(method) && !extraHeaders?.["X-Retry"]) {
        document.cookie = "stw_csrf=; Max-Age=0; path=/";
        document.cookie = "__Host-stw_csrf=; Max-Age=0; path=/; secure";
        return this.req<R>(method, path, body, { ...(extraHeaders ?? {}), "X-Retry": "1" });
      }
      throw err;
    }
    if (!res.ok) throw await toError(res);
    if (res.status === 204) return undefined as R;
    return (await res.json()) as R;
  }

  // auth
  me() { return this.req<T.Me>("GET", "/me"); }
  register(i: T.RegisterInput) { return this.req<{ message: string }>("POST", "/auth/register", i); }
  login(i: T.LoginInput) { return this.req<T.LoginResult>("POST", "/auth/login", i); }
  verifyMfa(code: string) { return this.req<T.Me>("POST", "/auth/mfa/verify", { code }); }
  async logout() { await this.req<void>("POST", "/auth/logout"); }
  verifyEmail(token: string) { return this.req<{ message: string }>("POST", "/auth/verify-email", { token }); }
  resendVerification(email: string) { return this.req<{ message: string }>("POST", "/auth/resend-verification", { email }); }
  forgotPassword(email: string) { return this.req<{ message: string }>("POST", "/auth/forgot-password", { email }); }
  resetPassword(token: string, password: string) { return this.req<{ message: string }>("POST", "/auth/reset-password", { token, password }); }
  completeOnboarding(i: T.OnboardingInput) { return this.req<T.Me>("POST", "/me/onboarding", i); }

  // two-step sign-in
  mfaStatus() { return this.req<T.MfaStatus>("GET", "/me/mfa"); }
  setupMfa(password: string) { return this.req<T.MfaSetup>("POST", "/me/mfa/setup", { password }); }
  enableMfa(code: string) { return this.req<T.RecoveryCodes>("POST", "/me/mfa/enable", { code }); }
  disableMfa(password: string, code: string) { return this.req<T.MfaStatus>("POST", "/me/mfa/disable", { password, code }); }
  regenerateRecoveryCodes(password: string, code: string) { return this.req<T.RecoveryCodes>("POST", "/me/mfa/recovery-codes", { password, code }); }

  dashboard() { return this.req<T.Dashboard>("GET", "/dashboard"); }
  getAgentStatus() { return this.req<T.AgentStatus>("GET", "/agent/status"); }
  getAgentTools() { return this.req<T.AgentTool[]>("GET", "/agent/tools"); }

  // tasks
  listTasks(q: T.TaskListQuery) { return this.req<T.Page<T.TaskSummary>>("GET", "/tasks" + qs(q)); }
  getTask(id: string) { return this.req<T.Task>("GET", `/tasks/${encodeURIComponent(id)}`); }
  createTask(i: T.CreateTaskInput, key: string) { return this.req<T.Task>("POST", "/tasks", i, { "Idempotency-Key": key }); }
  cancelTask(id: string, reason?: string) { return this.req<T.Task>("POST", `/tasks/${encodeURIComponent(id)}/cancel`, { reason: reason ?? null }); }
  pauseTask(id: string) { return this.req<T.Task>("POST", `/tasks/${encodeURIComponent(id)}/pause`); }
  resumeTask(id: string) { return this.req<T.Task>("POST", `/tasks/${encodeURIComponent(id)}/resume`); }
  postTaskMessage(id: string, text: string, kind: "question" | "answer" | "plan_change") {
    return this.req<T.Task>("POST", `/tasks/${encodeURIComponent(id)}/messages`, { text, kind });
  }
  approvePlan(id: string, selected: string[]) { return this.req<T.Task>("POST", `/tasks/${encodeURIComponent(id)}/plan/approve`, { selected_step_ids: selected }); }
  rejectPlan(id: string, note?: string) { return this.req<T.Task>("POST", `/tasks/${encodeURIComponent(id)}/plan/reject`, { note: note ?? null }); }
  listTaskEvents(id: string, page = 1) { return this.req<T.Page<T.TaskEvent>>("GET", `/tasks/${encodeURIComponent(id)}/events` + qs({ page, page_size: 100 })); }

  // approvals
  listApprovals(status: "pending" | "decided" | "all", page = 1) {
    return this.req<T.Page<T.Approval> & { pending_count: number }>("GET", "/approvals" + qs({ status, page, page_size: 50 }));
  }
  getApproval(id: string) { return this.req<T.Approval>("GET", `/approvals/${encodeURIComponent(id)}`); }
  decideApproval(id: string, i: T.DecisionInput) { return this.req<T.Approval>("POST", `/approvals/${encodeURIComponent(id)}/decision`, i); }

  // files
  uploadLimits() { return this.req<T.UploadLimits>("GET", "/files/limits"); }
  listFiles(q: T.FileListQuery) {
    return this.req<T.Page<T.FileItem> & { collections: { name: string; count: number }[] }>("GET", "/files" + qs(q));
  }
  getFile(id: string) { return this.req<T.FileItem>("GET", `/files/${encodeURIComponent(id)}`); }
  uploadFile(file: File, collection: string) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("collection", collection);
    return this.req<T.FileItem>("POST", "/files", fd);
  }
  updateFile(id: string, patch: { classification?: T.FileClassification; collection?: string }) {
    return this.req<T.FileItem>("PATCH", `/files/${encodeURIComponent(id)}`, patch);
  }
  requestFileDeletion(id: string, reason: string) { return this.req<T.Approval>("POST", `/files/${encodeURIComponent(id)}/deletion-request`, { reason }); }
  fileDownloadUrl(id: string) { return `${BASE}/files/${encodeURIComponent(id)}/download`; }
  restoreFile(id: string) { return this.req<T.FileItem>("POST", `/files/${encodeURIComponent(id)}/restore`); }
  artifactDownloadUrl(id: string) { return `${BASE}/artifacts/${encodeURIComponent(id)}/download`; }

  // automations
  listAutomations() { return this.req<T.Automation[]>("GET", "/automations"); }
  createAutomation(i: T.CreateAutomationInput) { return this.req<T.Automation>("POST", "/automations", i); }
  setAutomationEnabled(id: string, enabled: boolean) { return this.req<T.Automation>("PATCH", `/automations/${encodeURIComponent(id)}`, { enabled }); }
  runAutomation(id: string) { return this.req<T.Task>("POST", `/automations/${encodeURIComponent(id)}/run`); }

  // integrations
  listIntegrations() { return this.req<T.Integration[]>("GET", "/integrations"); }
  connectIntegration(key: string) { return this.req<T.Integration>("POST", `/integrations/${encodeURIComponent(key)}/connect`); }
  connectWordPress(siteUrl: string, credentials?: T.WordPressCredentials) {
    // Credentials are sent only when given (both fields); the server never returns the password.
    const body = credentials
      ? { site_url: siteUrl, username: credentials.username, application_password: credentials.application_password }
      : { site_url: siteUrl };
    return this.req<T.Integration>("POST", "/integrations/wordpress/connect", body);
  }
  disconnectIntegration(key: string) { return this.req<T.Integration>("POST", `/integrations/${encodeURIComponent(key)}/disconnect`); }

  // reports & activity
  listReports() { return this.req<{ artifacts: T.Artifact[]; reports: T.Task[] }>("GET", "/reports"); }
  listActivity(type?: T.ActivityType, page = 1) { return this.req<T.Page<T.ActivityEvent>>("GET", "/activity" + qs({ type, page, page_size: 50 })); }

  // notifications
  listNotifications(unreadOnly = false) {
    return this.req<{ items: T.NotificationItem[]; unread: number }>("GET", "/notifications" + qs({ unread: unreadOnly ? "true" : undefined }));
  }
  async markNotificationRead(id: string) { await this.req<void>("POST", `/notifications/${encodeURIComponent(id)}/read`); }
  async markAllNotificationsRead() { await this.req<void>("POST", "/notifications/read-all"); }

  // security & settings
  securityStatus() { return this.req<T.SecurityStatus>("GET", "/security/status"); }
  getSettings() { return this.req<T.Settings>("GET", "/settings"); }
  updatePreferences(i: T.PreferencesInput) { return this.req<T.Settings>("PATCH", "/me/preferences", i); }
  updateWorkspaceSettings(i: T.WorkspaceSettingsInput) { return this.req<T.Settings>("PATCH", "/workspace/settings", i); }
  usage() { return this.req<T.Usage>("GET", "/usage"); }
  requestDataExport() { return this.req<T.AccountRequest>("POST", "/account/export-request"); }
  requestAccountDeletion(reason: string) { return this.req<T.AccountRequest>("POST", "/account/deletion-request", { reason }); }
}

async function toError(res: Response): Promise<ApiError> {
  const rid = res.headers.get("X-Request-ID");
  try {
    const body = (await res.json()) as T.ApiErrorBody;
    if (body && body.error) {
      return new ApiError(res.status, body.error.code, body.error.message, body.error.request_id ?? rid, body.error.details ?? null);
    }
  } catch {
    /* non-JSON error body */
  }
  const msg =
    res.status === 429
      ? "Too many attempts. Please wait a few minutes and try again."
      : res.status >= 500
        ? "The server had a problem. Nothing was changed. Please try again."
        : "The request could not be completed.";
  return new ApiError(res.status, `http_${res.status}`, msg, rid);
}
