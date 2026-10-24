import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { Integration } from "@/lib/api/types";

const m = vi.hoisted(() => ({ connectWordPress: vi.fn(), disconnectIntegration: vi.fn(), toast: vi.fn() }));

vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a> }));
vi.mock("@/lib/session", () => ({ useUi: () => ({ toast: m.toast }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, DEMO_MODE: false, api: { connectWordPress: m.connectWordPress, disconnectIntegration: m.disconnectIntegration } };
});

import { IntegrationDrawer, checkWpCredentials } from "./IntegrationDrawer";

const wp: Integration = {
  key: "wordpress",
  name: "WordPress",
  icon: "globe",
  status: "not_connected",
  description: "Read pages and posts.",
  last_activity: "Never",
  destination: "Your WordPress website",
  scopes: ["Read published pages and posts (public REST API)"],
  connectable: true,
  operational: true,
  mode: null,
  site_url: null,
  error_message: null,
  write_access: false,
};
const APP_PW = "abcd EFGH 1234 ijkl MNOP 5678";

function renderDrawer(i: Integration = wp) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <IntegrationDrawer integration={i} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  m.connectWordPress.mockResolvedValue({ ...wp, site_url: "https://example.com" });
});
afterEach(cleanup);

describe("WordPress draft-post credentials", () => {
  it("is collapsed by default and connects read-only without credentials", async () => {
    renderDrawer();
    const toggle = screen.getByRole("button", { name: /also allow draft posts/i });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByLabelText("Application Password")).toBeNull();
    fireEvent.change(screen.getByLabelText(/site address/i), { target: { value: "https://example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /connect \(read-only\)/i }));
    await waitFor(() => expect(m.connectWordPress).toHaveBeenCalledWith("https://example.com", undefined));
  });

  it("requires both username and Application Password, or neither", async () => {
    renderDrawer();
    fireEvent.change(screen.getByLabelText(/site address/i), { target: { value: "https://example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /also allow draft posts/i }));
    const pw = screen.getByLabelText("Application Password");
    expect(pw.getAttribute("type")).toBe("password");
    expect(pw.getAttribute("autocomplete")).toBe("new-password")  // browsers ignore "off" on password fields;
    expect(screen.getByText(/Users → Profile → Application Passwords/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("WordPress username"), { target: { value: "editor" } });
    fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));
    expect(await screen.findByText(/enter both the wordpress username and the application password/i)).toBeTruthy();
    expect(m.connectWordPress).not.toHaveBeenCalled();

    fireEvent.change(pw, { target: { value: APP_PW } });
    fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));
    await waitFor(() => expect(m.connectWordPress).toHaveBeenCalledWith("https://example.com", { username: "editor", application_password: APP_PW }));
    // The password is cleared from the form once sent.
    await waitFor(() => expect((screen.getByLabelText("Application Password") as HTMLInputElement).value).toBe(""));
  });

  it("validates the pair the same way as the server", () => {
    expect(checkWpCredentials("", "")).toEqual({ creds: null, errors: {} });
    expect(checkWpCredentials("", APP_PW).errors.username).toMatch(/both/);
    expect(checkWpCredentials("editor", "").errors.application_password).toMatch(/both/);
    expect(checkWpCredentials("editor", "short").errors.application_password).toMatch(/exactly as WordPress shows it/);
    expect(checkWpCredentials(" editor ", ` ${APP_PW} `).creds).toEqual({ username: "editor", application_password: APP_PW });
  });

  it("shows Drafts enabled and the tool-switch reminder when write access is on", () => {
    renderDrawer({ ...wp, status: "connected", site_url: "https://example.com", write_access: true });
    expect(screen.getByText("Drafts enabled")).toBeTruthy();
    expect(screen.getAllByRole("link", { name: /wordpress write.*switch in settings/i }).length).toBeGreaterThan(0);
  });
});
