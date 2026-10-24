import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ApiError } from "@/lib/api/client";
import type { Me } from "@/lib/api/types";

const m = vi.hoisted(() => ({
  login: vi.fn(),
  verifyMfa: vi.fn(),
  resendVerification: vi.fn(),
  push: vi.fn(),
  setMe: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: m.push }), useSearchParams: () => new URLSearchParams() }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a> }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ setMe: m.setMe }), useUi: () => ({ toast: m.toast }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, DEMO_MODE: false, api: { login: m.login, verifyMfa: m.verifyMfa, resendVerification: m.resendVerification } };
});

import { LoginScreen, codeLooksValid } from "./LoginScreen";

const me: Me = {
  user: { id: "u1", name: "Ada", email: "ada@example.com", email_verified: true, onboarding_completed: true, created_at: "2026-01-01T00:00:00Z" },
  workspace: { id: "w1", name: "WS", role: "owner" },
  demo: false,
};

function renderLogin() {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LoginScreen />
    </QueryClientProvider>,
  );
}

async function enterPassword() {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse 1" } });
  fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));
  return screen.findByRole("heading", { name: /two-step sign-in/i });
}

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("LoginScreen two-step sign-in", () => {
  it("asks for a code after mfa_required, then signs in with the same redirect as a normal login", async () => {
    m.login.mockResolvedValue({ mfa_required: true, methods: ["totp", "recovery_code"] });
    m.verifyMfa.mockResolvedValue(me);
    renderLogin();
    await enterPassword();
    expect(m.setMe).not.toHaveBeenCalled(); // no session after the password alone
    const input = screen.getByLabelText("Authentication code");
    expect(input.getAttribute("autocomplete")).toBe("one-time-code");
    fireEvent.change(input, { target: { value: "123 456" } });
    fireEvent.click(screen.getByRole("button", { name: /verify and sign in/i }));
    await waitFor(() => expect(m.push).toHaveBeenCalledWith("/overview"));
    expect(m.verifyMfa).toHaveBeenCalledWith("123 456");
    expect(m.setMe).toHaveBeenCalledWith(me);
  });

  it("accepts a recovery code and shows a wrong code inline without leaving the step", async () => {
    m.login.mockResolvedValue({ mfa_required: true, methods: ["totp", "recovery_code"] });
    m.verifyMfa.mockRejectedValueOnce(new ApiError(401, "invalid_code", "That code didn't work."));
    renderLogin();
    await enterPassword();
    fireEvent.click(screen.getByRole("button", { name: /use a recovery code/i }));
    const input = screen.getByLabelText("Recovery code");
    expect(input.getAttribute("inputmode")).toBe("text");
    fireEvent.change(input, { target: { value: "abcd-efgh-ijkl" } });
    fireEvent.click(screen.getByRole("button", { name: /verify and sign in/i }));
    expect(await screen.findByText("That code didn't work.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: /two-step sign-in/i })).toBeTruthy();
    expect(m.verifyMfa).toHaveBeenCalledWith("abcd-efgh-ijkl");
    expect(m.push).not.toHaveBeenCalled();
  });

  it("goes back to the password step when the challenge has expired", async () => {
    m.login.mockResolvedValue({ mfa_required: true, methods: ["totp"] });
    m.verifyMfa.mockRejectedValueOnce(new ApiError(401, "mfa_challenge_expired", "Expired."));
    renderLogin();
    await enterPassword();
    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "654321" } });
    fireEvent.click(screen.getByRole("button", { name: /verify and sign in/i }));
    expect(await screen.findByText(/enter your password again/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /^sign in$/i })).toBeTruthy();
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("");
  });

  it("checks the code shape before sending it", async () => {
    m.login.mockResolvedValue({ mfa_required: true, methods: ["totp"] });
    renderLogin();
    await enterPassword();
    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: /verify and sign in/i }));
    expect(await screen.findByText("Enter the 6-digit code from your authenticator app.")).toBeTruthy();
    expect(m.verifyMfa).not.toHaveBeenCalled();
    expect(codeLooksValid("123456")).toBe(true);
    expect(codeLooksValid("ABCD efgh-ijkl")).toBe(true);
    expect(codeLooksValid("12345")).toBe(false);
  });

  it("signs in directly when the account has no second step", async () => {
    m.login.mockResolvedValue({ ...me, user: { ...me.user, onboarding_completed: false } });
    renderLogin();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));
    await waitFor(() => expect(m.push).toHaveBeenCalledWith("/onboarding"));
    expect(m.verifyMfa).not.toHaveBeenCalled();
  });
});
