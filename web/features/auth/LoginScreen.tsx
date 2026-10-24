"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, DEMO_MODE, errorMessage, isApiError, isMfaRequired, type LoginResult, type Me } from "@/lib/api";
import { emailOk } from "@/lib/format";
import { useSession, useUi } from "@/lib/session";
import { TextInput } from "@/components/ui";
import { AuthLayout } from "./AuthLayout";
import { AuthAlert, AuthButton, AuthField, AuthHeading, PasswordInput, SsoButton, safeNext, splitError } from "./parts";

/** 6 digits (spaces allowed) or a recovery code like abcd-efgh-ijkl. The server normalises case, spaces and dashes. */
export function codeLooksValid(raw: string): boolean {
  const c = raw.replace(/[\s-]/g, "");
  return /^\d{6}$/.test(c) || /^[a-z0-9]{8,32}$/i.test(c);
}

export function LoginScreen() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const { setMe } = useSession();
  const { toast } = useUi();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [formErr, setFormErr] = useState("");
  const [offerResend, setOfferResend] = useState(false);
  // Second step (two-step sign-in): shown on this same screen after the password is accepted.
  const [step, setStep] = useState<"password" | "code">("password");
  const [code, setCode] = useState("");
  const [recoveryMode, setRecoveryMode] = useState(false);
  const [codeErr, setCodeErr] = useState("");
  const codeRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (step === "code") codeRef.current?.focus();
  }, [step, recoveryMode]);

  const finish = (me: Me) => {
    // Drop anything cached for a previous account before storing the new session.
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
    setMe(me);
    setPassword("");
    setCode("");
    if (DEMO_MODE) toast("Signed in to the demo workspace. This is not real authentication.", "info");
    const next = safeNext(params.get("next"));
    router.push(next ?? (me.user.onboarding_completed ? "/overview" : "/onboarding"));
  };

  const backToPassword = (message = "") => {
    setStep("password");
    setCode("");
    setCodeErr("");
    setRecoveryMode(false);
    setErrs({});
    setFormErr(message);
    setOfferResend(false);
    verify.reset();
    // Let the password step render, then put focus back at the start of the form.
    setTimeout(() => emailRef.current?.focus(), 0);
  };

  const login = useMutation({
    mutationFn: () => api.login({ email: email.trim(), password }),
    onSuccess: (r: LoginResult) => {
      if (isMfaRequired(r)) {
        // Password accepted; no session yet. Don't keep the password around for the second step.
        setPassword("");
        setFormErr("");
        setErrs({});
        setCode("");
        setCodeErr("");
        setRecoveryMode(false);
        setStep("code");
        return;
      }
      finish(r);
    },
    onError: (e) => {
      const { fields, form } = splitError(e, ["email", "password"]);
      setErrs(fields);
      setFormErr(form);
      // The server uses one generic 401 for wrong password, unknown and unverified email.
      setOfferResend(isApiError(e) && e.status === 401);
    },
  });

  const verify = useMutation({
    mutationFn: () => api.verifyMfa(code.trim()),
    onSuccess: (me: Me) => finish(me),
    onError: (e) => {
      if (isApiError(e) && e.code === "mfa_challenge_expired") {
        backToPassword("This sign-in attempt timed out or was already used. Enter your password again to get a new one.");
        return;
      }
      if (isApiError(e) && e.code === "invalid_code") {
        setCodeErr(e.message || "That code didn't work. Check it and try again.");
        setFormErr("");
        setCode("");
        codeRef.current?.focus();
        return;
      }
      const { fields, form } = splitError(e, ["code"]);
      setCodeErr(fields.code ?? "");
      setFormErr(form);
    },
  });

  const resend = useMutation({
    mutationFn: () => api.resendVerification(email.trim()),
    onSuccess: (r) => toast(r.message || "If that account needs verifying, we sent a new link.", "success"),
    onError: (e) => toast(errorMessage(e), "danger"),
  });

  function submit(ev: FormEvent) {
    ev.preventDefault();
    const er: Record<string, string> = {};
    if (!emailOk(email.trim())) er.email = "Enter a valid email address.";
    if (!password) er.password = "Enter your password.";
    setErrs(er);
    setFormErr("");
    setOfferResend(false);
    if (Object.keys(er).length) return;
    login.mutate();
  }

  function submitCode(ev: FormEvent) {
    ev.preventDefault();
    setFormErr("");
    if (!codeLooksValid(code)) {
      setCodeErr(recoveryMode ? "Enter one of your recovery codes, like abcd-efgh-ijkl." : "Enter the 6-digit code from your authenticator app.");
      codeRef.current?.focus();
      return;
    }
    setCodeErr("");
    verify.mutate();
  }

  if (step === "code") {
    return (
      <AuthLayout>
        <form onSubmit={submitCode} noValidate className="flex flex-col gap-4" aria-labelledby="mfa-heading">
          <div>
            <h1 id="mfa-heading" className="text-[26px] font-semibold tracking-[-.015em]">
              Two-step sign-in
            </h1>
            <p className="mt-1.5 text-muted [text-wrap:pretty]">
              {recoveryMode
                ? "Enter one of the recovery codes you saved when you turned on two-step sign-in. Each code works once."
                : "Your password was accepted. Enter the 6-digit code from your authenticator app to finish signing in."}
              {DEMO_MODE && " Demo: use 123456 (there is no real authenticator)."}
            </p>
          </div>
          <div aria-live="polite">
            <AuthAlert>{formErr}</AuthAlert>
          </div>
          <AuthField label={recoveryMode ? "Recovery code" : "Authentication code"} htmlFor="login-code" error={codeErr} errorId="login-code-err">
            <TextInput
              ref={codeRef}
              id="login-code"
              name="code"
              type="text"
              // Numeric keypad for authenticator codes; a full keyboard for recovery codes (letters).
              inputMode={recoveryMode ? "text" : "numeric"}
              autoComplete="one-time-code"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              maxLength={32}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              invalid={!!codeErr}
              aria-describedby={codeErr ? "login-code-err" : undefined}
              placeholder={recoveryMode ? "abcd-efgh-ijkl" : "123 456"}
              className="font-mono tracking-[.08em]"
            />
          </AuthField>
          <AuthButton type="submit" loading={verify.isPending}>
            {verify.isPending ? "Checking…" : "Verify and sign in"}
          </AuthButton>
          <p className="text-[13px] text-muted">
            {recoveryMode ? "Have your phone? " : "Lost access to your authenticator app? "}
            <button
              type="button"
              onClick={() => {
                setRecoveryMode((v) => !v);
                setCodeErr("");
                setCode("");
              }}
              className="border-0 bg-transparent p-0 text-[13px] text-primary hover:underline"
            >
              {recoveryMode ? "Use an authenticator code" : "Use a recovery code"}
            </button>
          </p>
          <p className="text-center text-[13.5px]">
            <button type="button" onClick={() => backToPassword()} className="border-0 bg-transparent p-0 text-[13.5px] text-primary hover:underline">
              Back to sign in
            </button>
          </p>
        </form>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <AuthHeading title="Sign in">
          {DEMO_MODE ? (
            <>
              Demo sign-in: any email and password works. Use the password <span className="font-mono text-ink">error</span> to preview a failed sign-in.
            </>
          ) : (
            "Welcome back. Sign in to your workspace."
          )}
        </AuthHeading>
        <AuthAlert>{formErr}</AuthAlert>
        {formErr && offerResend && (
          <p className="-mt-2 text-[13px] text-muted">
            Haven&apos;t verified your email yet?{" "}
            <button
              type="button"
              onClick={() => resend.mutate()}
              disabled={resend.isPending || !emailOk(email.trim())}
              className="border-0 bg-transparent p-0 text-[13px] text-primary hover:underline disabled:opacity-60"
            >
              {resend.isPending ? "Sending…" : "Resend the verification link."}
            </button>
          </p>
        )}
        <AuthField label="Email" htmlFor="login-email" error={errs.email} errorId="login-email-err">
          <TextInput
            ref={emailRef}
            id="login-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            invalid={!!errs.email}
            aria-describedby={errs.email ? "login-email-err" : undefined}
            placeholder="you@example.com"
          />
        </AuthField>
        <AuthField
          label="Password"
          htmlFor="login-pw"
          labelExtra={
            <Link href="/forgot-password" className="font-normal">
              Forgot password?
            </Link>
          }
          error={errs.password}
          errorId="login-pw-err"
        >
          <PasswordInput
            id="login-pw"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            invalid={!!errs.password}
            aria-describedby={errs.password ? "login-pw-err" : undefined}
          />
        </AuthField>
        <AuthButton type="submit" loading={login.isPending}>
          {login.isPending ? "Signing in…" : "Sign in"}
        </AuthButton>
        <SsoButton />
        <p className="text-center text-[13.5px] text-muted">
          New here? <Link href="/register">{DEMO_MODE ? "Create a demo account" : "Create an account"}</Link>
        </p>
      </form>
    </AuthLayout>
  );
}
