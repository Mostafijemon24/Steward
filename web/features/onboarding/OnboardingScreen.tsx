"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, DEMO_MODE, errorMessage, type AiRules } from "@/lib/api";
import { INTEGRATION_STATUS } from "@/lib/labels";
import { useSession, useUi } from "@/lib/session";
import { Badge, EmptyState, ErrorState, Icon, Skeleton, Switch } from "@/components/ui";

const STEPS = ["Goals", "Where it works", "Important actions", "Connect tools", "Summary"];

// Stable values sent to the API; labels are display only.
const GOALS: { value: string; label: string; icon: string }[] = [
  { value: "wordpress", label: "WordPress maintenance", icon: "globe" },
  { value: "seo_content", label: "SEO & content", icon: "file-text" },
  { value: "research", label: "Research", icon: "search" },
  { value: "code", label: "Coding", icon: "code" },
  { value: "browser", label: "Browser workflows", icon: "monitor" },
  { value: "other", label: "Other", icon: "sparkles" },
];

const WHERE: { value: string; label: string; desc: string; icon: string }[] = [
  { value: "workspace_files", label: "Workspace files", desc: "Only files you add to this workspace.", icon: "folder" },
  { value: "connected_apps", label: "Connected apps", desc: "Tools like WordPress or Notion, after you connect them.", icon: "plug" },
  { value: "approved_websites", label: "Approved websites", desc: "Only domains on your allowlist.", icon: "globe" },
];

// The first four are shown on step 3; "folders" keeps its default (on).
const RULES: { key: keyof AiRules; label: string; desc: string }[] = [
  { key: "share", label: "Ask before sharing outside the workspace", desc: "Any file or text sent to an outside service needs your approval." },
  { key: "publish", label: "Ask before publishing or sending", desc: "Covers website posts, emails and messages." },
  { key: "prod", label: "Ask before changing live systems", desc: "Live website, store or database changes." },
  { key: "del", label: "Ask before deleting files or records", desc: "Each deletion needs its own approval listing the exact items." },
];

const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

export function OnboardingScreen() {
  const router = useRouter();
  const qc = useQueryClient();
  const { me, loading, signedOut, error, refresh, setMe } = useSession();
  const { toast } = useUi();
  const [step, setStep] = useState(0);
  const [goals, setGoals] = useState<string[]>(["wordpress"]);
  const [where, setWhere] = useState<string[]>(["workspace_files"]);
  const [rules, setRules] = useState<AiRules>({ share: true, publish: true, prod: true, del: true, folders: true });

  useEffect(() => {
    if (signedOut) router.replace("/login?next=/onboarding");
  }, [signedOut, router]);

  const integrations = useQuery({ queryKey: ["integrations"], queryFn: () => api.listIntegrations(), enabled: !!me });

  const finish = useMutation({
    mutationFn: () => api.completeOnboarding({ goals, where, ai_rules: rules }),
    onSuccess: async (updated) => {
      setMe(updated);
      void refresh();
      await qc.invalidateQueries({ queryKey: ["dashboard"] });
      void qc.invalidateQueries({ queryKey: ["settings"] });
      toast(DEMO_MODE ? "Setup saved for this demo session." : "Setup saved. You can change these anytime in Settings.", "success");
      router.push("/overview");
    },
    onError: (e) => toast(errorMessage(e), "danger"),
  });

  if (loading || signedOut || (!me && !error)) {
    return (
      <div className="flex min-h-[calc(100vh-34px)] justify-center px-4 py-8">
        <Skeleton lines={6} className="w-full max-w-[680px]" />
      </div>
    );
  }
  if (!me) {
    return (
      <div className="flex min-h-[calc(100vh-34px)] justify-center px-4 py-8">
        <div className="w-full max-w-[680px]">
          <ErrorState error={error} onRetry={() => void refresh()} title="We couldn't load your account" />
        </div>
      </div>
    );
  }

  const skip = () => {
    router.push("/overview");
    toast("Setup skipped. You can finish it from the Overview checklist.", "info");
  };
  const next = () => (step === 4 ? finish.mutate() : setStep(step + 1));
  const rulesOn = RULES.filter((r) => rules[r.key]).length;
  const connected = (integrations.data ?? []).filter((i) => i.status === "connected");

  const summary: { ok: boolean; label: string; value: string }[] = [
    { ok: true, label: "Goals", value: GOALS.filter((g) => goals.includes(g.value)).map((g) => g.label).join(", ") || "None selected" },
    { ok: true, label: "Where it works", value: WHERE.filter((w) => where.includes(w.value)).map((w) => w.label).join(", ") || "Nowhere yet" },
    {
      ok: true,
      label: "Important actions",
      value: DEMO_MODE
        ? `${rulesOn} of 4 safety approvals on — preferences until the backend enforces them`
        : `${rulesOn} of 4 safety approvals on — high-impact actions always ask, whatever these switches say`,
    },
    connected.length
      ? { ok: true, label: "Integrations", value: `${connected.map((i) => i.name).join(", ")} connected` }
      : { ok: false, label: "Integrations", value: DEMO_MODE ? "None connected — backend required" : "None connected — WordPress (read-only) can be connected later" },
    {
      ok: false,
      label: "Security",
      value: DEMO_MODE
        ? "Backend security controls are not configured yet"
        : "Account security and the policy engine are active. Malware scanning is not configured yet",
    },
  ];

  return (
    <div className="flex min-h-[calc(100vh-34px)] flex-col items-center px-4 py-8">
      <div className="mb-6 flex w-full max-w-[680px] items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span className="grid h-7 w-7 place-items-center rounded-[7px] bg-primary text-white">
            <Icon name="shield-check" size={16} />
          </span>
          <span className="text-base font-semibold">Steward</span>
        </div>
        <button type="button" onClick={skip} className="rounded-md border-0 bg-transparent px-2 py-1.5 text-sm text-ink-3 hover:bg-track">
          Skip setup
        </button>
      </div>

      <div className="w-full max-w-[680px] overflow-hidden rounded-xl border border-line bg-surface">
        <div className="px-7 pt-5">
          <div className="flex justify-between font-mono text-[12.5px] text-muted">
            <span>STEP {step + 1} OF 5</span>
            <span>{STEPS[step]}</span>
          </div>
          <div
            role="progressbar"
            aria-label="Setup progress"
            aria-valuenow={step + 1}
            aria-valuemin={1}
            aria-valuemax={5}
            className="mt-2.5 h-1 overflow-hidden rounded-sm bg-track"
          >
            <div className="h-full bg-primary transition-[width] duration-200 ease-out" style={{ width: `${((step + 1) / 5) * 100}%` }} />
          </div>
        </div>

        <div className="min-h-[340px] p-7">
          {step === 0 && (
            <>
              <StepTitle title="What do you want your AI Agent to help with?">Pick any. This only tailors suggestions — you can change it later.</StepTitle>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-2.5">
                {GOALS.map((g) => {
                  const on = goals.includes(g.value);
                  return (
                    <button
                      key={g.value}
                      type="button"
                      aria-pressed={on}
                      onClick={() => setGoals((l) => toggle(l, g.value))}
                      className="flex items-center gap-2.5 rounded-[10px] border p-3.5 text-left text-sm font-medium"
                      style={{ borderColor: on ? "#3448C5" : "#E4E7EC", background: on ? "#EEF1FC" : "#FFFFFF" }}
                    >
                      <Icon name={g.icon} size={18} className="text-primary" />
                      <span className="flex-1">{g.label}</span>
                      {on && <Icon name="check" size={16} className="text-primary" />}
                    </button>
                  );
                })}
              </div>
            </>
          )}

          {step === 1 && (
            <>
              <StepTitle title="Where should it work?">The Agent can only use places you allow. Everything else stays off-limits.</StepTitle>
              <div className="flex flex-col gap-2.5">
                {WHERE.map((w) => {
                  const on = where.includes(w.value);
                  return (
                    <button
                      key={w.value}
                      type="button"
                      aria-pressed={on}
                      onClick={() => setWhere((l) => toggle(l, w.value))}
                      className="flex items-start gap-3 rounded-[10px] border px-4 py-3.5 text-left"
                      style={{ borderColor: on ? "#3448C5" : "#E4E7EC", background: on ? "#EEF1FC" : "#FFFFFF" }}
                    >
                      <Icon name={w.icon} size={18} className="mt-0.5 text-primary" />
                      <span className="flex-1">
                        <span className="block text-sm font-semibold">{w.label}</span>
                        <span className="mt-0.5 block text-[13.5px] text-muted">{w.desc}</span>
                      </span>
                      <span
                        className="mt-0.5 grid h-[18px] w-[18px] flex-none place-items-center rounded border-[1.5px] text-white"
                        style={{ borderColor: on ? "#3448C5" : "#E4E7EC", background: on ? "#3448C5" : "#FFFFFF" }}
                      >
                        {on && <Icon name="check" size={12} />}
                      </span>
                    </button>
                  );
                })}
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <StepTitle title="How should it handle important actions?">
                {DEMO_MODE
                  ? "We recommend keeping all of these on. They are preferences until the backend policy engine enforces them."
                  : "We recommend keeping all of these on. Publishing, sending, deleting and live changes always need approval regardless; these switches can only make the rules stricter."}
              </StepTitle>
              <div className="flex flex-col rounded-[10px] border border-line">
                {RULES.map((r) => (
                  <div key={r.key} className="flex items-center gap-4 border-b border-line-soft px-4 py-3.5 last:border-b-0">
                    <div className="flex-1">
                      <div className="font-medium">{r.label}</div>
                      <div className="text-[13px] text-muted">{r.desc}</div>
                    </div>
                    <Switch on={rules[r.key]} label={r.label} onToggle={() => setRules((s) => ({ ...s, [r.key]: !s[r.key] }))} />
                  </div>
                ))}
              </div>
            </>
          )}

          {step === 3 && (
            <>
              <StepTitle title="Connect tools later">
                {DEMO_MODE
                  ? "Connections need a secure backend flow, so none can be set up in this prototype. You'll never be asked to paste a password or API key into chat."
                  : "WordPress (read-only without credentials; optionally draft posts with a WordPress Application Password) can be connected from the Integrations page after setup. Other integrations are not built yet. You'll never be asked to paste a password or API key into chat."}
              </StepTitle>
              {integrations.isLoading ? (
                <Skeleton lines={4} />
              ) : integrations.isError ? (
                <ErrorState error={integrations.error} onRetry={() => void integrations.refetch()} retrying={integrations.isFetching} />
              ) : !integrations.data?.length ? (
                <EmptyState icon="plug" title="No integrations available">
                  Integrations will appear here once they are available for your workspace.
                </EmptyState>
              ) : (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-2.5">
                  {integrations.data.map((it) => (
                    <div key={it.key} className="flex flex-col gap-2 rounded-[10px] border border-line p-3.5">
                      <Icon name={it.icon} size={18} className="text-ink-3" />
                      <span className="font-medium">{it.name}</span>
                      <Badge meta={INTEGRATION_STATUS[it.status]} className="self-start" />
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {step === 4 && (
            <>
              <StepTitle title="Your setup summary">Here is what is and isn&apos;t configured.</StepTitle>
              <ul className="m-0 flex list-none flex-col rounded-[10px] border border-line p-0">
                {summary.map((x) => (
                  <li key={x.label} className="flex items-start gap-3 border-b border-line-soft px-4 py-3 last:border-b-0">
                    <Icon
                      name={x.ok ? "circle-check" : "circle-alert"}
                      size={17}
                      className={x.ok ? "mt-0.5 text-success" : "mt-0.5 text-warning"}
                      label={x.ok ? "Configured" : "Not configured"}
                    />
                    <div className="flex-1">
                      <div className="font-medium">{x.label}</div>
                      <div className="text-[13px] text-muted">{x.value}</div>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>

        <div className="flex justify-between gap-3 border-t border-line bg-subtle px-7 py-4">
          <button
            type="button"
            onClick={() => setStep(Math.max(0, step - 1))}
            disabled={step === 0}
            className="h-[38px] rounded-lg border border-line-strong bg-surface px-4 text-sm font-medium disabled:opacity-50"
          >
            Back
          </button>
          <button
            type="button"
            onClick={next}
            disabled={finish.isPending}
            aria-busy={finish.isPending || undefined}
            className="inline-flex h-[38px] items-center gap-2 rounded-lg border-0 bg-primary px-[18px] text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-70"
          >
            {step === 4 ? (finish.isPending ? "Saving…" : "Go to workspace") : "Continue"}
            <Icon name={finish.isPending ? "loader" : "arrow-right"} size={15} spin={finish.isPending} />
          </button>
        </div>
      </div>
    </div>
  );
}

function StepTitle({ title, children }: { title: string; children: ReactNode }) {
  return (
    <>
      <h1 className="text-[22px] font-semibold tracking-[-.01em]">{title}</h1>
      <p className="mt-1.5 mb-5 text-muted">{children}</p>
    </>
  );
}
