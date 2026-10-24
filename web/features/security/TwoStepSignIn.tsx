"use client";

import { useId, useRef, useState, type FormEvent, type ReactNode, type Ref } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, DEMO_MODE, errorMessage, isApiError, type MfaSetup, type RecoveryCodes } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { useUi } from "@/lib/session";
import { Badge, Banner, Button, Checkbox, ErrorState, FieldError, Label, Modal, Skeleton, TextInput } from "@/components/ui";

/** Recovery codes at or below this count trigger a warning. */
export const LOW_RECOVERY_CODES = 3;

const DEMO_CODE_HINT = "Demo: use the code 123456 — there is no real authenticator.";

// ------------------------------------------------------------------ small helpers
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** "JBSWY3DPEHPK3PXP…" → "JBSW Y3DP EHPK …" for easier manual typing. */
const groupKey = (k: string) => k.replace(/(.{4})/g, "$1 ").trim();

/** Splits an API error into password / code field errors and a form-level message. */
function fieldErrorsOf(error: unknown) {
  if (!isApiError(error)) return { password: "", code: "", form: error ? errorMessage(error) : "" };
  if (error.code === "invalid_password") return { password: error.message || "That password isn't right.", code: "", form: "" };
  if (error.code === "invalid_code") return { password: "", code: error.message || "That code didn't work. Check it and try again.", form: "" };
  const fe = error.fieldErrors();
  if (fe.password || fe.code) return { password: fe.password ?? "", code: fe.code ?? "", form: "" };
  return { password: "", code: "", form: errorMessage(error) };
}

function PasswordField({ id, value, onChange, error, autoFocus }: { id: string; value: string; onChange: (v: string) => void; error?: string; autoFocus?: boolean }) {
  return (
    <div>
      <Label htmlFor={id}>Your password</Label>
      <TextInput
        id={id}
        type="password"
        autoComplete="current-password"
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        invalid={!!error}
        aria-describedby={error ? `${id}-err` : undefined}
      />
      <FieldError id={`${id}-err`}>{error}</FieldError>
    </div>
  );
}

function CodeField({
  id,
  value,
  onChange,
  error,
  allowRecovery,
  inputRef,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  error?: string;
  allowRecovery?: boolean;
  inputRef?: Ref<HTMLInputElement>;
}) {
  const hint = allowRecovery ? "6-digit code from your app, or a recovery code" : "6-digit code from your app";
  return (
    <div>
      <Label htmlFor={id} hint={hint}>
        Authentication code
      </Label>
      <TextInput
        ref={inputRef}
        id={id}
        type="text"
        inputMode={allowRecovery ? "text" : "numeric"}
        autoComplete="one-time-code"
        autoCapitalize="none"
        spellCheck={false}
        maxLength={32}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        invalid={!!error}
        aria-describedby={error ? `${id}-err` : undefined}
        className="font-mono tracking-[.08em]"
        placeholder={allowRecovery ? "123456 or abcd-efgh-ijkl" : "123456"}
      />
      <FieldError id={`${id}-err`}>{error}</FieldError>
    </div>
  );
}

function FormAlert({ children }: { children: ReactNode }) {
  return (
    <div aria-live="polite">
      {children ? (
        <Banner tone="danger" icon="circle-alert" role="alert">
          {children}
        </Banner>
      ) : null}
    </div>
  );
}

const sixDigits = (c: string) => /^\d{6}$/.test(c.replace(/\s/g, ""));
const codeOrRecovery = (c: string) => {
  const n = c.replace(/[\s-]/g, "");
  return /^\d{6}$/.test(n) || /^[a-z0-9]{8,32}$/i.test(n);
};

// ------------------------------------------------------------------ recovery codes (shown once)
export function RecoveryCodesPanel({ result, saved, onSaved }: { result: RecoveryCodes; saved: boolean; onSaved: (v: boolean) => void }) {
  const { toast } = useUi();
  const ackId = useId();
  const text = result.recovery_codes.join("\n");
  const fileText =
    "Steward recovery codes\n" +
    `Created ${new Date().toISOString()}\n` +
    "Each code can be used once to sign in if you lose your authenticator app.\n\n" +
    text +
    "\n";
  return (
    <div className="flex flex-col gap-3">
      <Banner tone="warning" icon="triangle-alert" title="Save these recovery codes now">
        They won&apos;t be shown again. Each code works once, if you lose access to your authenticator app. Keep them somewhere safe, like a password manager.
      </Banner>
      {result.message && <p className="m-0 text-[13.5px] text-ink-2 [text-wrap:pretty]">{result.message}</p>}
      <ul aria-label="Recovery codes" className="m-0 grid list-none grid-cols-2 gap-x-4 gap-y-1.5 rounded-lg border border-line bg-subtle p-3.5 font-mono text-[14px]">
        {result.recovery_codes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          icon="copy"
          onClick={async () => toast((await copyText(text)) ? "Recovery codes copied." : "Couldn't copy. Select the codes and copy them by hand.", "info")}
        >
          Copy codes
        </Button>
        <Button size="sm" icon="download" onClick={() => downloadText("steward-recovery-codes.txt", fileText)}>
          Download .txt
        </Button>
      </div>
      <Checkbox id={ackId} checked={saved} onChange={onSaved}>
        I saved my recovery codes
      </Checkbox>
    </div>
  );
}

// ------------------------------------------------------------------ turn on
function TurnOnModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const { toast } = useUi();
  const pwId = useId();
  const codeId = useId();
  const codeRef = useRef<HTMLInputElement>(null);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [localErr, setLocalErr] = useState<{ password?: string; code?: string }>({});
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [codes, setCodes] = useState<RecoveryCodes | null>(null);
  const [saved, setSaved] = useState(false);

  const start = useMutation({
    mutationFn: () => api.setupMfa(password),
    onSuccess: (r) => {
      setPassword("");
      setSetup(r);
      void qc.invalidateQueries({ queryKey: ["mfa"] });
    },
    onError: (e) => {
      if (isApiError(e) && e.code === "mfa_already_enabled") void qc.invalidateQueries({ queryKey: ["mfa"] });
    },
  });
  const enable = useMutation({
    mutationFn: () => api.enableMfa(code.trim()),
    onSuccess: (r) => {
      setCode("");
      setSetup(null); // the secret isn't needed any more
      setCodes(r);
      void qc.invalidateQueries({ queryKey: ["mfa"] });
      void qc.invalidateQueries({ queryKey: ["security"] });
      void qc.invalidateQueries({ queryKey: ["activity"] });
    },
    onError: (e) => {
      if (isApiError(e) && e.code === "invalid_code") {
        setCode("");
        codeRef.current?.focus();
      }
    },
  });

  const startErr = fieldErrorsOf(start.error);
  const enableErr = fieldErrorsOf(enable.error);
  const busy = start.isPending || enable.isPending;
  // While the codes are on screen, closing needs the "I saved" tick so they aren't lost by accident.
  const canClose = !busy && (!codes || saved);
  const close = () => {
    if (!canClose) return;
    if (codes) toast("Two-step sign-in is on.", "success");
    onClose();
  };

  const submitPassword = (e: FormEvent) => {
    e.preventDefault();
    if (!password) return setLocalErr({ password: "Enter your password." });
    setLocalErr({});
    start.mutate();
  };
  const submitCode = (e: FormEvent) => {
    e.preventDefault();
    if (!sixDigits(code)) return setLocalErr({ code: "Enter the 6-digit code your app shows now." });
    setLocalErr({});
    enable.mutate();
  };

  let body: ReactNode;
  let footer: ReactNode;
  if (codes) {
    body = <RecoveryCodesPanel result={codes} saved={saved} onSaved={setSaved} />;
    footer = (
      <Button variant="primary" disabled={!saved} onClick={close}>
        Done
      </Button>
    );
  } else if (setup) {
    body = (
      <form id="mfa-enable-form" onSubmit={submitCode} noValidate className="flex flex-col gap-3.5">
        <ol className="m-0 flex list-decimal flex-col gap-1 pl-5 text-[13.5px] text-ink-2">
          <li>Open an authenticator app on your phone (for example 1Password, Google Authenticator, Microsoft Authenticator or Authy).</li>
          <li>Scan the QR code, or type the key below by hand.</li>
          <li>Enter the 6-digit code the app shows.</li>
        </ol>
        {DEMO_MODE && (
          <Banner tone="info" icon="flask-conical">
            Demo: the picture below is a placeholder, not a scannable code. {DEMO_CODE_HINT}
          </Banner>
        )}
        <div className="flex flex-wrap items-start gap-4">
          {/* eslint-disable-next-line @next/next/no-img-element -- data: URI from the server, not an optimisable asset */}
          <img
            src={setup.qr_svg_data_uri}
            alt="QR code to add this account to your authenticator app"
            width={180}
            height={180}
            className="rounded-lg border border-line bg-white p-1.5"
          />
          <div className="flex min-w-0 flex-[1_1_200px] flex-col gap-1.5">
            <span className="text-[13px] font-medium">Can&apos;t scan? Enter this key</span>
            <code className="break-all rounded-lg border border-line bg-subtle px-3 py-2 font-mono text-[13.5px] tracking-[.04em]" aria-label="Setup key">
              {groupKey(setup.secret)}
            </code>
            <div>
              <Button
                size="xs"
                icon="copy"
                onClick={async () => toast((await copyText(setup.secret)) ? "Key copied." : "Couldn't copy. Select the key and copy it by hand.", "info")}
              >
                Copy key
              </Button>
            </div>
            <span className="text-[12.5px] text-muted">Time-based (TOTP), 6 digits, 30 seconds. Keep this key private.</span>
          </div>
        </div>
        <CodeField id={codeId} inputRef={codeRef} value={code} onChange={setCode} error={localErr.code ?? enableErr.code} />
        <FormAlert>{enableErr.form}</FormAlert>
      </form>
    );
    footer = (
      <>
        <Button onClick={close} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" form="mfa-enable-form" variant="primary" loading={enable.isPending}>
          Turn on
        </Button>
      </>
    );
  } else {
    body = (
      <form id="mfa-setup-form" onSubmit={submitPassword} noValidate className="flex flex-col gap-3.5">
        <p className="m-0 text-[13.5px] text-ink-2 [text-wrap:pretty]">
          After your password, you&apos;ll also be asked for a code from an authenticator app on your phone. Confirm your password to start.
          {DEMO_MODE && " Demo: any password except “error” works."}
        </p>
        <PasswordField id={pwId} value={password} onChange={setPassword} error={localErr.password ?? startErr.password} autoFocus />
        <FormAlert>{startErr.form}</FormAlert>
      </form>
    );
    footer = (
      <>
        <Button onClick={close} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" form="mfa-setup-form" variant="primary" loading={start.isPending}>
          Continue
        </Button>
      </>
    );
  }

  return (
    <Modal title={codes ? "Your recovery codes" : "Turn on two-step sign-in"} onClose={close} footer={footer} maxWidth={560}>
      <div className="p-5">{body}</div>
    </Modal>
  );
}

// ------------------------------------------------------------------ new recovery codes
function RegenerateModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const { toast } = useUi();
  const pwId = useId();
  const codeId = useId();
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [localErr, setLocalErr] = useState<{ password?: string; code?: string }>({});
  const [codes, setCodes] = useState<RecoveryCodes | null>(null);
  const [saved, setSaved] = useState(false);
  const m = useMutation({
    mutationFn: () => api.regenerateRecoveryCodes(password, code.trim()),
    onSuccess: (r) => {
      setPassword("");
      setCode("");
      setCodes(r);
      void qc.invalidateQueries({ queryKey: ["mfa"] });
      void qc.invalidateQueries({ queryKey: ["activity"] });
    },
  });
  const err = fieldErrorsOf(m.error);
  const canClose = !m.isPending && (!codes || saved);
  const close = () => {
    if (!canClose) return;
    if (codes) toast("New recovery codes saved. The old ones no longer work.", "success");
    onClose();
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const le: { password?: string; code?: string } = {};
    if (!password) le.password = "Enter your password.";
    if (!codeOrRecovery(code)) le.code = "Enter the 6-digit code from your app, or a recovery code.";
    setLocalErr(le);
    if (Object.keys(le).length) return;
    m.mutate();
  };

  return (
    <Modal
      title={codes ? "Your new recovery codes" : "Create new recovery codes"}
      onClose={close}
      maxWidth={540}
      footer={
        codes ? (
          <Button variant="primary" disabled={!saved} onClick={close}>
            Done
          </Button>
        ) : (
          <>
            <Button onClick={close} disabled={m.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="mfa-regen-form" variant="primary" loading={m.isPending}>
              Create new codes
            </Button>
          </>
        )
      }
    >
      <div className="p-5">
        {codes ? (
          <RecoveryCodesPanel result={codes} saved={saved} onSaved={setSaved} />
        ) : (
          <form id="mfa-regen-form" onSubmit={submit} noValidate className="flex flex-col gap-3.5">
            <p className="m-0 text-[13.5px] text-ink-2 [text-wrap:pretty]">
              You&apos;ll get 10 new codes. <strong>All your current recovery codes stop working.</strong>
              {DEMO_MODE && ` ${DEMO_CODE_HINT}`}
            </p>
            <PasswordField id={pwId} value={password} onChange={setPassword} error={localErr.password ?? err.password} autoFocus />
            <CodeField id={codeId} value={code} onChange={setCode} error={localErr.code ?? err.code} allowRecovery />
            <FormAlert>{err.form}</FormAlert>
          </form>
        )}
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ turn off
function TurnOffModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const { toast } = useUi();
  const pwId = useId();
  const codeId = useId();
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [localErr, setLocalErr] = useState<{ password?: string; code?: string }>({});
  const m = useMutation({
    mutationFn: () => api.disableMfa(password, code.trim()),
    onSuccess: (status) => {
      qc.setQueryData(["mfa"], status);
      void qc.invalidateQueries({ queryKey: ["mfa"] });
      void qc.invalidateQueries({ queryKey: ["security"] });
      void qc.invalidateQueries({ queryKey: ["activity"] });
      toast(DEMO_MODE ? "Two-step sign-in is off (demo)." : "Two-step sign-in is off. Your password alone now signs you in.", "info");
      onClose();
    },
  });
  const err = fieldErrorsOf(m.error);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const le: { password?: string; code?: string } = {};
    if (!password) le.password = "Enter your password.";
    if (!codeOrRecovery(code)) le.code = "Enter the 6-digit code from your app, or a recovery code.";
    setLocalErr(le);
    if (Object.keys(le).length) return;
    m.mutate();
  };
  return (
    <Modal
      title="Turn off two-step sign-in?"
      onClose={m.isPending ? () => {} : onClose}
      maxWidth={520}
      footer={
        <>
          <Button onClick={onClose} disabled={m.isPending}>
            Keep it on
          </Button>
          <Button type="submit" form="mfa-disable-form" variant="danger-solid" loading={m.isPending}>
            Turn off
          </Button>
        </>
      }
    >
      <form id="mfa-disable-form" onSubmit={submit} noValidate className="flex flex-col gap-3.5 p-5">
        <Banner tone="warning" icon="shield-alert" title="Your account becomes less protected">
          Anyone who learns your password will be able to sign in without a code. Your recovery codes will stop working.
        </Banner>
        {DEMO_MODE && <p className="m-0 text-[13px] text-muted">{DEMO_CODE_HINT}</p>}
        <PasswordField id={pwId} value={password} onChange={setPassword} error={localErr.password ?? err.password} autoFocus />
        <CodeField id={codeId} value={code} onChange={setCode} error={localErr.code ?? err.code} allowRecovery />
        <FormAlert>{err.form}</FormAlert>
      </form>
    </Modal>
  );
}

// ------------------------------------------------------------------ card
export function TwoStepSignInCard() {
  const q = useQuery({ queryKey: ["mfa"], queryFn: () => api.mfaStatus() });
  const [modal, setModal] = useState<"on" | "off" | "codes" | null>(null);

  let content: ReactNode;
  if (q.isLoading) content = <Skeleton lines={3} />;
  else if (q.isError || !q.data)
    content = <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} title="We couldn't load your two-step sign-in status" />;
  else {
    const s = q.data;
    const low = s.enabled && s.recovery_codes_remaining <= LOW_RECOVERY_CODES;
    content = (
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-[15px] font-semibold">Two-step sign-in</h3>
          {s.enabled ? (
            <Badge meta={{ fg: "#16803C", bg: "#ECFDF3", icon: "shield-check" }}>On</Badge>
          ) : (
            <Badge meta={{ fg: "#475467", bg: "#F2F4F7", icon: "shield-alert" }}>Off</Badge>
          )}
        </div>
        <p className="m-0 text-[13.5px] text-ink-2 [text-wrap:pretty]">
          After your password, ask for a code from an authenticator app on your phone. Someone who learns your password still can&apos;t sign in without it.
          {DEMO_MODE && " Demo: kept in this browser tab only."}
        </p>
        {s.enabled ? (
          <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[13.5px]">
            <dt className="text-muted">Turned on</dt>
            <dd className="m-0">{s.enabled_at ? fmtDateTime(s.enabled_at) : "—"}</dd>
            <dt className="text-muted">Recovery codes left</dt>
            <dd className="m-0" style={{ color: low ? "#B54708" : undefined }}>
              {s.recovery_codes_remaining} of 10
            </dd>
          </dl>
        ) : (
          s.pending_setup && (
            <p className="m-0 text-[13px] text-muted">Setup was started but not finished. Turning it on starts again with a new key.</p>
          )
        )}
        {low && (
          <Banner tone="warning" icon="triangle-alert">
            {s.recovery_codes_remaining === 0
              ? "You have no recovery codes left. If you lose your phone you could be locked out — create new codes now."
              : `Only ${s.recovery_codes_remaining} recovery code${s.recovery_codes_remaining === 1 ? "" : "s"} left. Create new ones so you don't get locked out.`}
          </Banner>
        )}
        <div className="flex flex-wrap gap-2">
          {s.enabled ? (
            <>
              <Button size="sm" icon="key-round" onClick={() => setModal("codes")}>
                Create new recovery codes
              </Button>
              <Button size="sm" variant="danger" onClick={() => setModal("off")}>
                Turn off
              </Button>
            </>
          ) : (
            <Button size="sm" variant="primary" icon="shield-check" onClick={() => setModal("on")}>
              Turn on
            </Button>
          )}
        </div>
      </div>
    );
  }

  return (
    <section aria-label="Two-step sign-in" className="rounded-[10px] border border-line p-4">
      {content}
      {modal === "on" && <TurnOnModal onClose={() => setModal(null)} />}
      {modal === "off" && <TurnOffModal onClose={() => setModal(null)} />}
      {modal === "codes" && <RegenerateModal onClose={() => setModal(null)} />}
    </section>
  );
}
