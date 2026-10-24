import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api/client";
import type { MfaStatus } from "@/lib/api/types";

const m = vi.hoisted(() => ({
  mfaStatus: vi.fn(),
  setupMfa: vi.fn(),
  enableMfa: vi.fn(),
  disableMfa: vi.fn(),
  regenerateRecoveryCodes: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("@/lib/session", () => ({ useUi: () => ({ toast: m.toast }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    DEMO_MODE: false,
    api: { mfaStatus: m.mfaStatus, setupMfa: m.setupMfa, enableMfa: m.enableMfa, disableMfa: m.disableMfa, regenerateRecoveryCodes: m.regenerateRecoveryCodes },
  };
});

import { TwoStepSignInCard } from "./TwoStepSignIn";

const OFF: MfaStatus = { enabled: false, pending_setup: false, enabled_at: null, recovery_codes_remaining: 0 };
const CODES = Array.from({ length: 10 }, (_, i) => `abcd-efgh-ij${String(i).padStart(2, "0")}`);

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TwoStepSignInCard />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("TwoStepSignInCard", () => {
  it("turns on: password → QR + manual key + code → recovery codes that must be acknowledged", async () => {
    m.mfaStatus.mockResolvedValue(OFF);
    m.setupMfa.mockResolvedValue({ secret: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/x", qr_svg_data_uri: "data:image/svg+xml;base64,PHN2Zy8+" });
    m.enableMfa.mockResolvedValue({ recovery_codes: CODES, message: "Two-step sign-in is on. Other sessions were signed out." });
    renderCard();

    expect(await screen.findByText("Off")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /turn on/i }));
    const dialog = await screen.findByRole("dialog");

    fireEvent.change(within(dialog).getByLabelText("Your password"), { target: { value: "my password 1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /continue/i }));
    const qr = await within(dialog).findByRole("img", { name: /qr code/i });
    expect(qr.getAttribute("src")).toMatch(/^data:image\/svg\+xml/);
    expect(m.setupMfa).toHaveBeenCalledWith("my password 1");
    expect(within(dialog).getByLabelText("Setup key").textContent).toBe("JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP");

    fireEvent.change(within(dialog).getByLabelText(/authentication code/i), { target: { value: "123456" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^turn on$/i }));

    const list = await within(dialog).findByRole("list", { name: /recovery codes/i });
    expect(within(list).getAllByRole("listitem")).toHaveLength(10);
    expect(within(dialog).getByText(/won.t be shown again/i)).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: /copy codes/i })).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: /download \.txt/i })).toBeTruthy();
    expect(m.enableMfa).toHaveBeenCalledWith("123456");

    // Can't close until "I saved my recovery codes" is ticked.
    const done = within(dialog).getByRole("button", { name: /^done$/i }) as HTMLButtonElement;
    expect(done.disabled).toBe(true);
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeTruthy();
    fireEvent.click(within(dialog).getByLabelText(/i saved my recovery codes/i));
    expect(done.disabled).toBe(false);
    fireEvent.click(done);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("shows a wrong password next to the field", async () => {
    m.mfaStatus.mockResolvedValue(OFF);
    m.setupMfa.mockRejectedValue(new ApiError(401, "invalid_password", "That password isn't right."));
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: /turn on/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Your password"), { target: { value: "nope" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /continue/i }));
    expect(await within(dialog).findByText("That password isn't right.")).toBeTruthy();
  });

  it("warns when few recovery codes are left and offers new codes and turning off", async () => {
    m.mfaStatus.mockResolvedValue({ enabled: true, pending_setup: false, enabled_at: "2026-10-01T10:00:00Z", recovery_codes_remaining: 2 });
    renderCard();
    expect(await screen.findByText("On")).toBeTruthy();
    expect(screen.getByText(/only 2 recovery codes left/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /create new recovery codes/i })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^turn off$/i }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/less protected/i)).toBeTruthy();
  });
});
