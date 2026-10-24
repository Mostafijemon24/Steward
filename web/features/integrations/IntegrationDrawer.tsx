"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, DEMO_MODE, errorMessage, isApiError, type Integration, type WordPressCredentials } from "@/lib/api";
import { useUi } from "@/lib/session";
import { Badge, Button, Drawer, FieldError, Icon, Label, TextInput } from "@/components/ui";
import { integrationBadge, isChecking } from "./status";

const HTTPS_SITE = /^https:\/\/[a-z0-9.-]+\.[a-z]{2,63}(\/[^\s?#]*)?$/i;

export interface CredentialErrors {
  username?: string;
  application_password?: string;
}

/**
 * Same rules as the server: both fields or neither; the Application Password is
 * pasted as WordPress shows it (24 letters/digits, usually in groups of four).
 */
export function checkWpCredentials(username: string, password: string): { creds: WordPressCredentials | null; errors: CredentialErrors } {
  const u = username.trim();
  const p = password.trim();
  if (!u && !p) return { creds: null, errors: {} };
  const both = "Enter both the WordPress username and the Application Password, or leave both empty for read-only.";
  if (!u) return { creds: null, errors: { username: both } };
  if (!p) return { creds: null, errors: { application_password: both } };
  if (p.length < 16 || p.length > 100 || /[\r\n\t]/.test(p)) {
    return { creds: null, errors: { application_password: "Paste the Application Password exactly as WordPress shows it, like abcd EFGH 1234 ijkl MNOP 6789." } };
  }
  return { creds: { username: u, application_password: p }, errors: {} };
}

export function IntegrationDrawer({ integration: i, onClose }: { integration: Integration; onClose: () => void }) {
  const { toast } = useUi();
  const qc = useQueryClient();
  const [site, setSite] = useState(i.site_url ?? "");
  const [siteErr, setSiteErr] = useState("");
  const [showDrafts, setShowDrafts] = useState(false);
  const [wpUser, setWpUser] = useState("");
  const [wpPass, setWpPass] = useState("");
  const [credErr, setCredErr] = useState<CredentialErrors>({});
  const isWp = i.key === "wordpress";
  const isProvider = i.key === "ai_provider";
  const notBuilt = !i.operational && !isProvider;
  const canRemove = !isProvider && (i.status === "demo" || i.status === "connected" || i.status === "error" || !!i.site_url);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["integrations"] });
    void qc.invalidateQueries({ queryKey: ["dashboard"] });
    void qc.invalidateQueries({ queryKey: ["activity"] });
  };

  const connect = useMutation({
    mutationFn: ({ url, creds }: { url: string; creds: WordPressCredentials | null }) => api.connectWordPress(url, creds ?? undefined),
    onSuccess: (_r, vars) => {
      refresh();
      // The password is never kept in the browser after it is sent.
      setWpPass("");
      if (DEMO_MODE) toast(vars.creds ? "Demo connection saved with drafts allowed. Nothing was contacted and the password was not stored." : "Demo connection saved (nothing was contacted).", "info");
      else toast(vars.creds ? "Checking the site and the Application Password… It shows as connected once the check succeeds." : "Checking the site… It shows as connected once the check succeeds.", "info");
    },
    onError: (e) => {
      const fe = isApiError(e) ? e.fieldErrors() : {};
      if (isApiError(e) && (fe.site_url || e.code === "domain_not_allowed")) setSiteErr(fe.site_url ?? e.message);
      else if (fe.username || fe.application_password) setCredErr({ username: fe.username, application_password: fe.application_password });
      else toast(errorMessage(e), "danger");
    },
  });
  const disconnect = useMutation({
    mutationFn: () => api.disconnectIntegration(i.key),
    onSuccess: () => {
      refresh();
      toast(`${i.name} disconnected.`, "info");
    },
    onError: (e) => toast(errorMessage(e), "danger"),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const v = site.trim();
    if (!HTTPS_SITE.test(v)) {
      setSiteErr("Enter the site's https:// address, like https://example.com");
      return;
    }
    setSiteErr("");
    let creds: WordPressCredentials | null = null;
    if (showDrafts) {
      const r = checkWpCredentials(wpUser, wpPass);
      setCredErr(r.errors);
      if (Object.keys(r.errors).length) return;
      creds = r.creds;
    } else setCredErr({});
    connect.mutate({ url: v, creds });
  };

  return (
    <Drawer
      label="Manage integration"
      onClose={onClose}
      header={
        <>
          <Icon name={i.icon} size={18} className="text-ink-3" />
          <h2 className="m-0 min-w-0 flex-1 text-[15px] font-semibold">{i.name}</h2>
          <Badge meta={integrationBadge(i)} />
        </>
      }
      footer={
        canRemove ? (
          <div className="flex w-full flex-wrap justify-end gap-2">
            <Button variant="danger" size="sm" className="h-9!" loading={disconnect.isPending} onClick={() => disconnect.mutate()}>
              Disconnect
            </Button>
          </div>
        ) : undefined
      }
    >
      <p className="m-0 text-ink-2 [text-wrap:pretty]">{i.description}</p>
      {i.mode && (
        <div className="text-[13px] text-muted">
          How it works: <span className="text-ink-2">{i.mode}</span>
        </div>
      )}

      {notBuilt && (
        <div role="note" className="flex gap-2 rounded-lg border border-line bg-subtle px-3.5 py-3 text-[13px] text-ink-3">
          <Icon name="info" size={14} className="mt-0.5 text-primary" />
          <span>Not built yet. This integration can&apos;t be connected, and the Agent can&apos;t use it.</span>
        </div>
      )}

      {isProvider && (
        <div role="note" className="flex gap-2 rounded-lg border border-line bg-subtle px-3.5 py-3 text-[13px] text-ink-3">
          <Icon name="key-round" size={14} className="mt-0.5 text-primary" />
          <span>The AI model is configured by your server administrator. API keys are never entered in the browser, and they are never shown to the model or written to logs.</span>
        </div>
      )}

      {isWp && (
        <form onSubmit={submit} noValidate className="flex flex-col gap-2 rounded-lg border border-line px-3.5 py-3">
          {isChecking(i) && (
            <div role="status" className="flex items-center gap-2 text-[13px] text-info">
              <Icon name="loader" size={14} spin />
              Checking the site…
            </div>
          )}
          {i.status === "error" && i.error_message && (
            <div role="alert" className="flex items-start gap-2 text-[13px] text-danger">
              <Icon name="triangle-alert" size={14} className="mt-0.5" />
              {i.error_message}
            </div>
          )}
          {i.status === "connected" && i.site_url && (
            <div className="flex items-center gap-2 text-[13px] text-success">
              <Icon name="circle-check" size={14} />
              <span>
                Reading <span className="font-mono">{i.site_url}</span>
                {i.write_access ? "" : " (read-only)"}
              </span>
            </div>
          )}
          {i.write_access && (
            <div className="flex flex-col gap-1 rounded-md bg-success-soft px-2.5 py-2 text-[13px]">
              <span className="flex items-center gap-1.5 font-medium text-success">
                <Icon name="pencil" size={13} />
                Drafts enabled
              </span>
              <span className="text-ink-3 [text-wrap:pretty]">
                The Agent may create draft posts (never publish, edit or delete), each after your approval of the exact text. The{" "}
                <Link href="/settings?tab=ai">“WordPress write” switch in Settings</Link> must also be on.
              </span>
            </div>
          )}
          <Label htmlFor="wp-site" hint="Read-only without credentials, through the site's public REST API.">
            Site address
          </Label>
          <TextInput
            id="wp-site"
            type="url"
            inputMode="url"
            placeholder="https://example.com"
            value={site}
            onChange={(e) => setSite(e.target.value)}
            invalid={!!siteErr}
            aria-describedby={siteErr ? "wp-site-err" : undefined}
            autoComplete="url"
          />
          <FieldError id="wp-site-err">{siteErr}</FieldError>
          <p className="m-0 text-[12.5px] text-muted [text-wrap:pretty]">
            The site&apos;s domain must be on Settings → Allowed domains first. The Agent only contacts domains on that list, and the site is checked before it shows as connected.
          </p>

          <div className="rounded-md border border-line-soft">
            <button
              type="button"
              aria-expanded={showDrafts}
              aria-controls="wp-drafts"
              onClick={() => setShowDrafts((v) => !v)}
              className="flex w-full items-center gap-2 border-0 bg-transparent px-2.5 py-2 text-left text-[13.5px] font-medium text-ink hover:bg-subtle"
            >
              <Icon name={showDrafts ? "chevron-down" : "chevron-right"} size={14} />
              Also allow draft posts (optional)
            </button>
            {showDrafts && (
              <div id="wp-drafts" className="flex flex-col gap-2.5 border-t border-line-soft px-2.5 pb-3 pt-2.5">
                <p className="m-0 text-[12.5px] text-ink-3 [text-wrap:pretty]">
                  Uses a WordPress <strong>Application Password</strong> — a separate password just for this app, not your login password. Create one in WordPress under{" "}
                  <span className="whitespace-nowrap">Users → Profile → Application Passwords</span>. You can revoke it there at any time.
                </p>
                <ul className="m-0 flex list-none flex-col gap-1 p-0 text-[12.5px] text-ink-3">
                  {[
                    "The Agent can only create drafts. It can't publish, edit or delete anything.",
                    "Each draft needs your one-time approval of the exact text first.",
                    "The password is stored encrypted on the server and never shown again — not even to you. Disconnecting deletes it.",
                  ].map((t) => (
                    <li key={t} className="flex gap-1.5">
                      <Icon name="check" size={13} className="mt-[2px] text-success" />
                      {t}
                    </li>
                  ))}
                </ul>
                <div>
                  <Label htmlFor="wp-user">WordPress username</Label>
                  <TextInput
                    id="wp-user"
                    value={wpUser}
                    onChange={(e) => setWpUser(e.target.value)}
                    autoComplete="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    maxLength={60}
                    invalid={!!credErr.username}
                    aria-describedby={credErr.username ? "wp-user-err" : undefined}
                  />
                  <FieldError id="wp-user-err">{credErr.username}</FieldError>
                </div>
                <div>
                  <Label htmlFor="wp-app-pw">Application Password</Label>
                  <TextInput
                    id="wp-app-pw"
                    type="password"
                    value={wpPass}
                    onChange={(e) => setWpPass(e.target.value)}
                    // "new-password": browsers ignore "off" for password fields and may offer the
                    // user's own saved password here; this is a WordPress app credential instead.
                    autoComplete="new-password"
                    spellCheck={false}
                    maxLength={100}
                    placeholder="xxxx xxxx xxxx xxxx xxxx xxxx"
                    invalid={!!credErr.application_password}
                    aria-describedby={credErr.application_password ? "wp-app-pw-err" : undefined}
                  />
                  <FieldError id="wp-app-pw-err">{credErr.application_password}</FieldError>
                </div>
                <p className="m-0 text-[12.5px] text-muted [text-wrap:pretty]">
                  The <Link href="/settings?tab=ai">“WordPress write” switch in Settings</Link> must also be on (it&apos;s off by default). Leave both fields empty to stay read-only.
                </p>
              </div>
            )}
          </div>
          {i.write_access && !showDrafts && (
            <p className="m-0 text-[12.5px] text-warning [text-wrap:pretty]">
              Saving without the Application Password switches back to read-only and deletes the stored password.
            </p>
          )}
          <div>
            <Button type="submit" variant="primary" size="sm" className="h-9!" loading={connect.isPending}>
              {i.site_url ? "Save and re-check" : showDrafts && (wpUser || wpPass) ? "Connect" : "Connect (read-only)"}
            </Button>
          </div>
        </form>
      )}

      <div>
        <div className="mb-2 text-[13px] font-medium">Permissions (plain language)</div>
        {i.scopes.length > 0 ? (
          <ul className="m-0 list-none rounded-lg border border-line p-0">
            {i.scopes.map((x) => (
              <li key={x} className="flex gap-2 border-b border-line-soft px-3 py-[9px] text-[13.5px] last:border-b-0">
                <Icon name="check" size={14} className="mt-[3px] text-success" />
                {x}
              </li>
            ))}
          </ul>
        ) : (
          <div className="rounded-lg border border-line px-3 py-[9px] text-[13.5px] text-muted">No permissions requested.</div>
        )}
      </div>
      <div className="flex gap-2 rounded-lg bg-primary-soft px-3.5 py-3 text-[13px] text-[#1F2A7A]">
        <Icon name="send" size={14} className="mt-0.5" />
        <span>Data destination: {i.destination}. Being connected doesn&apos;t allow sharing every file — each step is checked by the policy engine.</span>
      </div>
      <div className="text-[13px] text-muted">Last check: {i.last_activity}</div>
    </Drawer>
  );
}
