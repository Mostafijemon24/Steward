"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  DEMO_MODE,
  errorMessage,
  isApiError,
  type AiRules,
  type NotificationPrefs,
  type PreferencesInput,
  type Settings,
  type ToolPermissions,
  type WorkspaceSettingsInput,
} from "@/lib/api";
import { useSession, useUi } from "@/lib/session";
import { TIMEZONES } from "@/lib/labels";
import { Banner, Button, ErrorState, FieldError, Icon, MonoTag, Select, Skeleton, Switch, TextInput, cx } from "@/components/ui";
import { AccountDeletionModal, DataExportModal } from "@/features/security/AccountRequestModals";
import { TwoStepSignInCard } from "@/features/security/TwoStepSignIn";

const TABS = [
  { k: "profile", l: "Profile", icon: "user" },
  { k: "workspace", l: "Workspace", icon: "building-2" },
  { k: "security", l: "Security", icon: "shield-check" },
  { k: "appearance", l: "Appearance", icon: "palette" },
  { k: "notifications", l: "Notifications", icon: "bell" },
  { k: "ai", l: "AI behaviour", icon: "bot" },
  { k: "usage", l: "Usage & limits", icon: "gauge" },
  { k: "billing", l: "Billing", icon: "credit-card" },
  { k: "account", l: "Account", icon: "user-cog" },
] as const;
type TabKey = (typeof TABS)[number]["k"];

const NOTIF_RULES: [keyof NotificationPrefs, string][] = [
  ["approval", "Approval requested"],
  ["done", "Task completed"],
  ["failed", "Task failed or needs input"],
  ["security", "Security policy blocked an action"],
  ["digest", DEMO_MODE ? "Daily summary email (needs backend email)" : "Daily summary email (not sent yet)"],
];

const AI_RULES: [keyof AiRules, string, string][] = [
  ["share", "Ask before sharing outside the workspace", "In custom privacy mode, turning this off lets public and internal data go to the configured AI model without asking. Confidential data always asks."],
  ["publish", "Ask before publishing or sending", "Covers website posts, emails and messages."],
  ["prod", "Ask before changing live systems", "Always on in practice: production changes always need approval."],
  ["del", "Ask before deleting files or records", "Always on in practice: each deletion needs its own approval listing the exact items."],
  ["folders", "Restrict work to selected folders", "Saved preference. Today the Agent reads only files attached to the task."],
];

const TOOL_PERMS: [keyof ToolPermissions, string][] = [
  ["wp_read", "Read WordPress content"],
  ["wp_write", "WordPress write: create draft posts only (never publishes; each draft needs your approval)"],
  ["browser", "Read web pages on allowed domains (inspection and SEO audit)"],
  ["email", "Send email (not built yet)"],
  ["external", "Allow sending data to an external AI model (each transfer still checked; confidential data asks)"],
];

const NOT_EDITABLE = "Only workspace owners and admins can change this.";
const DOMAIN_RE = /^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/i;

// ------------------------------------------------------------------ shared bits
function H2({ children, className, tag }: { children: ReactNode; className?: string; tag?: ReactNode }) {
  return (
    <div className={cx("flex flex-wrap items-center gap-2", className ?? "mb-4")}>
      <h2 className="text-base font-semibold">{children}</h2>
      {tag}
    </div>
  );
}

function Field({ label, hint, error, children }: { label: string; hint?: ReactNode; error?: string; children: (id: string, describedBy?: string) => ReactNode }) {
  const id = useId();
  const describedBy = [hint ? `${id}-hint` : "", error ? `${id}-err` : ""].filter(Boolean).join(" ") || undefined;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-medium">
        {label}
      </label>
      {children(id, describedBy)}
      {hint && (
        <span id={`${id}-hint`} className="text-[12.5px] text-muted">
          {hint}
        </span>
      )}
      <FieldError id={`${id}-err`}>{error}</FieldError>
    </div>
  );
}

function SaveButton({ loading, disabled, children = "Save changes", onClick }: { loading: boolean; disabled?: boolean; children?: ReactNode; onClick: () => void }) {
  return (
    <Button variant="primary" size="sm" loading={loading} disabled={disabled} onClick={onClick} className="h-9 self-start px-3.5">
      {children}
    </Button>
  );
}

function SwitchRow({ label, desc, on, onToggle, disabled, dense }: { label: string; desc?: string; on: boolean; onToggle: () => void; disabled?: boolean; dense?: boolean }) {
  return (
    <div className={cx("flex items-center gap-4 border-t border-line-soft", dense ? "py-2.5" : "py-3")}>
      {desc ? (
        <div className="flex-1">
          <div className="font-medium">{label}</div>
          <div className="text-[13px] text-muted">{desc}</div>
        </div>
      ) : (
        <span className={cx("flex-1", dense && "text-[13.5px]")}>{label}</span>
      )}
      <Switch on={on} onToggle={onToggle} label={label} disabled={disabled} />
    </div>
  );
}

function Placeholder({ icon, title, children }: { icon: string; title: string; children: ReactNode }) {
  return (
    <div className="rounded-[10px] border border-dashed border-line-strong px-5 py-8 text-center text-muted">
      <Icon name={icon} size={22} className="mx-auto" />
      <div className="mt-1.5 font-semibold text-ink">{title}</div>
      <div className="text-[13.5px]">{children}</div>
    </div>
  );
}

function ReadOnlyNote() {
  return (
    <Banner tone="info" icon="lock" className="mb-4">
      {NOT_EDITABLE}
    </Banner>
  );
}

/** Saves through the API, writes the fresh settings into the cache, and reports honestly. */
function useSave<I>(fn: (input: I) => Promise<Settings>, opts: { policy?: boolean; extraKeys?: string[][] } = {}) {
  const qc = useQueryClient();
  const { toast } = useUi();
  return useMutation({
    mutationFn: fn,
    onSuccess: (res) => {
      qc.setQueryData(["settings"], res);
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      for (const k of opts.extraKeys ?? []) qc.invalidateQueries({ queryKey: k });
      if (DEMO_MODE) toast("Saved for this browser session only. Nothing is sent to a server.", "success");
      else toast(opts.policy && !res.enforced ? "Saved. Not enforced on this server." : opts.policy ? "Saved. The policy engine uses this from the next step on." : "Saved.", "success");
    },
    onError: (e) => {
      if (!(isApiError(e) && Object.keys(e.fieldErrors()).length)) toast(errorMessage(e), "danger");
    },
  });
}

function FormError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <Banner tone="danger" icon="circle-alert" role="alert">
      {errorMessage(error)}
    </Banner>
  );
}

// ------------------------------------------------------------------ sections
function ProfileSection({ s }: { s: Settings }) {
  const [name, setName] = useState(s.profile.name);
  const [tz, setTz] = useState(s.profile.timezone);
  const [touched, setTouched] = useState(false);
  const m = useSave((i: PreferencesInput) => api.updatePreferences(i), { extraKeys: [["me"]] });
  const fe = isApiError(m.error) ? m.error.fieldErrors() : {};
  const nameErr = touched && !name.trim() ? "Enter your name." : fe.name;
  const tzOpts = TIMEZONES.includes(tz) ? TIMEZONES : [tz, ...TIMEZONES];
  const save = () => {
    setTouched(true);
    if (!name.trim()) return;
    m.mutate({ name: name.trim(), timezone: tz });
  };
  return (
    <>
      <H2>Profile</H2>
      <div className="flex max-w-[440px] flex-col gap-3.5">
        <Field label="Display name" error={nameErr}>
          {(id, d) => <TextInput id={id} aria-describedby={d} value={name} maxLength={100} invalid={!!nameErr} onChange={(e) => setName(e.target.value)} className="h-[38px]" />}
        </Field>
        <Field label="Email" hint={DEMO_MODE ? "Changing email needs verification by the backend." : "Changing your email isn't available yet."}>
          {(id, d) => <TextInput id={id} aria-describedby={d} value={s.profile.email} readOnly className="h-[38px] !border-line bg-subtle text-muted" />}
        </Field>
        <Field label="Timezone" error={fe.timezone}>
          {(id, d) => (
            <Select id={id} aria-describedby={d} value={tz} onChange={(e) => setTz(e.target.value)} className="h-[38px] px-2.5">
              {tzOpts.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {m.isError && !Object.keys(fe).length && <FormError error={m.error} />}
        <SaveButton loading={m.isPending} onClick={save} />
      </div>
    </>
  );
}

function WorkspaceSection({ s }: { s: Settings }) {
  const { me } = useSession();
  const can = s.workspace.can_edit;
  const [name, setName] = useState(s.workspace.name);
  const [domains, setDomains] = useState(s.workspace.allowed_domains.join(", "));
  const [touched, setTouched] = useState(false);
  const m = useSave((i: WorkspaceSettingsInput) => api.updateWorkspaceSettings(i), { policy: true, extraKeys: [["me"], ["security"]] });
  const fe = isApiError(m.error) ? m.error.fieldErrors() : {};
  const list = domains
    .split(",")
    .map((d) => d.trim().toLowerCase())
    .filter(Boolean);
  const bad = list.filter((d) => !DOMAIN_RE.test(d));
  const nameErr = touched && !name.trim() ? "Enter a workspace name." : fe.name;
  const domErr = touched && bad.length ? `Not a valid domain: ${bad.join(", ")}` : fe.allowed_domains;
  const save = () => {
    setTouched(true);
    if (!name.trim() || bad.length) return;
    m.mutate({ name: name.trim(), allowed_domains: Array.from(new Set(list)) });
  };
  return (
    <>
      <H2 tag={!s.enforced ? <MonoTag>NOT ENFORCED</MonoTag> : undefined}>Workspace</H2>
      {!can && <ReadOnlyNote />}
      <div className="flex max-w-[440px] flex-col gap-3.5">
        <Field label="Workspace name" error={nameErr}>
          {(id, d) => (
            <TextInput id={id} aria-describedby={d} value={name} maxLength={100} disabled={!can} invalid={!!nameErr} onChange={(e) => setName(e.target.value)} className="h-[38px] disabled:bg-subtle" />
          )}
        </Field>
        <Field
          label="Allowed domains for browser tasks"
          error={domErr}
          hint={s.enforced ? "Comma-separated. The Agent can reach only these domains (and their subdomains); every other domain is blocked." : "Comma-separated."}
        >
          {(id, d) => (
            <TextInput
              id={id}
              aria-describedby={d}
              value={domains}
              disabled={!can}
              invalid={!!domErr}
              placeholder="example.com, shop.example.com"
              onChange={(e) => setDomains(e.target.value)}
              className="h-[38px] font-mono disabled:bg-subtle"
            />
          )}
        </Field>
        <div className="text-[13px] text-muted">
          {DEMO_MODE
            ? "Members and roles: one member (you). Inviting others needs the backend."
            : `Members and roles: one member (you${me ? `, ${me.workspace.role}` : ""}). Inviting others isn't available yet.`}
        </div>
        {m.isError && !Object.keys(fe).length && <FormError error={m.error} />}
        <SaveButton loading={m.isPending} disabled={!can} onClick={save} />
      </div>
    </>
  );
}

function NotificationsSection({ s }: { s: Settings }) {
  const [prefs, setPrefs] = useState<NotificationPrefs>(s.notifications);
  const m = useSave((i: PreferencesInput) => api.updatePreferences(i));
  return (
    <>
      <H2 className="mb-1.5">Notifications</H2>
      <p className="mb-3 text-[13.5px] text-muted">{DEMO_MODE ? "In-app notifications. Email delivery needs the backend." : "Choose which in-app notifications you get."}</p>
      {NOTIF_RULES.map(([k, l]) => (
        <SwitchRow key={k} label={l} on={prefs[k]} onToggle={() => setPrefs((p) => ({ ...p, [k]: !p[k] }))} />
      ))}
      <div className="mt-3 flex flex-col gap-3">
        {m.isError && <FormError error={m.error} />}
        <SaveButton loading={m.isPending} onClick={() => m.mutate({ notifications: prefs })} />
      </div>
    </>
  );
}

function AiSection({ s }: { s: Settings }) {
  const can = s.workspace.can_edit;
  const [rules, setRules] = useState<AiRules>(s.workspace.ai_rules);
  const [tools, setTools] = useState<ToolPermissions>(s.workspace.tool_permissions);
  const m = useSave((i: WorkspaceSettingsInput) => api.updateWorkspaceSettings(i), { policy: true, extraKeys: [["security"]] });
  return (
    <>
      <H2 className="mb-1.5" tag={!s.enforced ? <MonoTag>NOT ENFORCED</MonoTag> : undefined}>
        AI behaviour
      </H2>
      <p className="mb-3 text-[13.5px] text-muted [text-wrap:pretty]">
        {s.enforced
          ? "The policy engine reads these before every step. The “ask before” rules can only make it stricter: actions that always need approval (publishing, sending, deleting, production changes, code, confidential data leaving) keep needing it even if a switch is off."
          : DEMO_MODE
            ? "Your preferences for this demo session. In the real app the server's policy engine enforces them."
            : "Your preferences are saved, but this server reports they are not enforced."}
      </p>
      {!can && <ReadOnlyNote />}
      {AI_RULES.map(([k, l, d]) => (
        <SwitchRow key={k} label={l} desc={d} on={rules[k]} disabled={!can} onToggle={() => setRules((r) => ({ ...r, [k]: !r[k] }))} />
      ))}
      <div className="mb-1 mt-[18px] text-[13px] font-medium">Allowed tool permissions</div>
      {TOOL_PERMS.map(([k, l]) => (
        <SwitchRow key={k} dense label={l} on={tools[k]} disabled={!can} onToggle={() => setTools((t) => ({ ...t, [k]: !t[k] }))} />
      ))}
      <p className="mt-2 text-[12.5px] text-muted">
        Allowed domains for browser tasks are set under{" "}
        <Link href="/settings?tab=workspace" replace>
          Workspace
        </Link>
        .
      </p>
      <div className="mt-3 flex flex-col gap-3">
        {m.isError && <FormError error={m.error} />}
        <SaveButton loading={m.isPending} disabled={!can} onClick={() => m.mutate({ ai_rules: rules, tool_permissions: tools })}>
          Save preferences
        </SaveButton>
      </div>
    </>
  );
}

function UsageSection() {
  const q = useQuery({ queryKey: ["usage"], queryFn: () => api.usage() });
  return (
    <>
      <H2>Usage &amp; limits</H2>
      {q.isLoading ? (
        <Skeleton lines={4} />
      ) : q.isError || !q.data ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} retrying={q.isFetching} title="We couldn't load usage" />
      ) : !q.data.tracked ? (
        <Placeholder icon="gauge" title={DEMO_MODE ? "Usage tracking requires the backend" : "Usage tracking isn't switched on yet"}>
          Task counts, AI usage and storage limits will appear here. No numbers are shown so nothing looks real when it isn&apos;t.
        </Placeholder>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="text-[13px] text-muted">Period: {q.data.period}</div>
          {q.data.items.map((it) => {
            const pct = it.limit ? Math.min(100, Math.round((it.used / it.limit) * 100)) : null;
            return (
              <div key={it.key} className="flex flex-col gap-1.5">
                <div className="flex justify-between gap-3 text-[13.5px]">
                  <span className="font-medium">{it.label}</span>
                  <span className="text-muted">
                    {it.used.toLocaleString()}
                    {it.limit != null ? ` of ${it.limit.toLocaleString()}` : ""} {it.unit}
                  </span>
                </div>
                {pct != null && (
                  <div className="h-1.5 overflow-hidden rounded-sm bg-track" role="progressbar" aria-label={it.label} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                    <div className="h-full" style={{ width: `${pct}%`, background: pct >= 90 ? "#B42318" : "#3448C5" }} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

function AccountSection() {
  const [modal, setModal] = useState<"export" | "delete" | null>(null);
  return (
    <>
      <H2>Account</H2>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] border border-line p-3.5">
          <div>
            <div className="font-medium">Export your data</div>
            <div className="text-[13px] text-muted">Receive a copy of tasks, files and logs.</div>
          </div>
          <Button size="sm" className="text-[13px]" onClick={() => setModal("export")}>
            Request export
          </Button>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] border border-line p-3.5">
          <div>
            <div className="font-medium">Sign out of all sessions</div>
            <div className="text-[13px] text-muted [text-wrap:pretty]">
              Signing out ends this session only. Resetting your password signs you out everywhere.
            </div>
          </div>
          <Link
            href="/forgot-password"
            className="inline-flex h-[34px] items-center rounded-lg border border-line-strong bg-surface px-3 text-[13px] font-medium text-ink hover:bg-subtle hover:text-ink hover:no-underline"
          >
            Reset password
          </Link>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] border border-danger-line p-3.5">
          <div>
            <div className="font-medium text-danger">Delete account</div>
            <div className="text-[13px] text-muted">Permanently removes your workspace after a review and waiting period.</div>
          </div>
          <Button size="sm" variant="danger" className="text-[13px]" onClick={() => setModal("delete")}>
            Request deletion
          </Button>
        </div>
        <p className="text-[12.5px] text-muted">
          These are the same requests as on{" "}
          <Link href="/security">Security &amp; Privacy</Link>.
        </p>
      </div>
      {modal === "export" && <DataExportModal onClose={() => setModal(null)} />}
      {modal === "delete" && <AccountDeletionModal onClose={() => setModal(null)} />}
    </>
  );
}

function SecuritySection() {
  return (
    <>
      <H2 className="mb-1.5">Security</H2>
      <p className="mb-4 text-[13.5px] text-muted">How you sign in to your own account. Workspace privacy rules are on <Link href="/security">Security &amp; Privacy</Link>.</p>
      <TwoStepSignInCard />
    </>
  );
}

// ------------------------------------------------------------------ page
export function SettingsPage() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const raw = params.get("tab");
  const tab: TabKey = (TABS.find((t) => t.k === raw)?.k ?? "profile") as TabKey;
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api.getSettings() });
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const pick = (k: TabKey) => router.replace(`${pathname}?tab=${k}`, { scroll: false });
  const onKey = (e: KeyboardEvent, i: number) => {
    const d = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const n = (i + d + TABS.length) % TABS.length;
    refs.current[n]?.focus();
    pick(TABS[n].k);
  };

  const needsSettings = tab === "profile" || tab === "workspace" || tab === "notifications" || tab === "ai";
  let body: ReactNode;
  if (needsSettings && q.isLoading) body = <Skeleton lines={5} />;
  else if (needsSettings && (q.isError || !q.data)) body = <ErrorState error={q.error} onRetry={() => q.refetch()} retrying={q.isFetching} title="We couldn't load your settings" />;
  else if (tab === "profile") body = <ProfileSection key="p" s={q.data!} />;
  else if (tab === "workspace") body = <WorkspaceSection key="w" s={q.data!} />;
  else if (tab === "notifications") body = <NotificationsSection key="n" s={q.data!} />;
  else if (tab === "ai") body = <AiSection key="a" s={q.data!} />;
  else if (tab === "usage") body = <UsageSection />;
  else if (tab === "account") body = <AccountSection />;
  else if (tab === "security") body = <SecuritySection />;
  else if (tab === "billing")
    body = (
      <>
        <H2>Billing</H2>
        <Placeholder icon="credit-card" title="Coming later">
          Billing is not part of this MVP.
        </Placeholder>
      </>
    );
  else
    body = (
      <>
        <H2>Appearance</H2>
        <div className="flex flex-wrap gap-2.5">
          <div className="w-[180px] rounded-[10px] border-[1.5px] border-primary bg-primary-soft p-2.5">
            <div className="h-16 rounded-md border border-line bg-page" />
            <div className="mt-2 text-[13.5px] font-medium">Light</div>
            <div className="text-xs text-muted">Current</div>
          </div>
          <div className="w-[180px] rounded-[10px] border border-line p-2.5 opacity-60">
            <div className="h-16 rounded-md bg-ink" />
            <div className="mt-2 text-[13.5px] font-medium">Dark</div>
            <div className="text-xs text-muted">Coming later</div>
          </div>
        </div>
      </>
    );

  return (
    <div data-screen-label="Settings" className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-[-.015em]">Settings</h1>
        <p className="mt-1 text-muted">
          {DEMO_MODE
            ? "Changes are kept for this browser session only."
            : q.data && !q.data.enforced
              ? "Changes are saved to your account, but this server reports policy settings are not enforced."
              : "Changes are saved to your account."}
        </p>
      </div>
      <div className="flex flex-wrap items-start gap-4">
        <div role="tablist" aria-label="Settings sections" aria-orientation="vertical" className="flex min-w-[180px] flex-[0_1_220px] flex-col gap-0.5">
          {TABS.map((t, i) => {
            const active = t.k === tab;
            return (
              <button
                key={t.k}
                ref={(el) => {
                  refs.current[i] = el;
                }}
                type="button"
                role="tab"
                id={`settings-tab-${t.k}`}
                aria-selected={active}
                aria-controls="settings-panel"
                tabIndex={active ? 0 : -1}
                onClick={() => pick(t.k)}
                onKeyDown={(e) => onKey(e, i)}
                className={cx(
                  "flex h-9 items-center gap-2.5 rounded-lg border-0 px-2.5 text-left text-sm",
                  active ? "bg-primary-soft text-primary" : "bg-transparent text-ink-2 hover:bg-line-soft",
                )}
              >
                <Icon name={t.icon} size={16} />
                {t.l}
              </button>
            );
          })}
        </div>
        <div
          id="settings-panel"
          role="tabpanel"
          aria-labelledby={`settings-tab-${tab}`}
          className="min-w-0 flex-[999_1_480px] rounded-[10px] border border-line bg-surface px-[22px] py-5"
        >
          {body}
        </div>
      </div>
    </div>
  );
}
