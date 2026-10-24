// End-to-end: turn on two-step sign-in in Settings, then sign in with an authenticator code and
// with a recovery code, against the REAL backend (not demo mode).
// Prereqs: API + web running (WEB_URL, default http://localhost:3000), MAIL_OUTBOX_DIR readable.
// Usage: node e2e/mfa.mjs [--shots <dir>]
import { createRequire } from "node:module";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const { chromium } = createRequire(import.meta.url)("playwright");
const BASE = process.env.WEB_URL ?? "http://localhost:3000";
const OUTBOX = process.env.MAIL_OUTBOX_DIR ?? path.resolve("api/var/mail-outbox");
const shotsIdx = process.argv.indexOf("--shots");
const SHOTS = shotsIdx > 0 ? process.argv[shotsIdx + 1] : null;
const email = `e2e-mfa-${Date.now()}@example.com`;
const password = "e2e-password-123";
const steps = [];
const consoleErrors = [];

// RFC 6238 TOTP (SHA-1, 6 digits, 30 s) — what authenticator apps compute.
function totp(secretB32, offset = 0) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secretB32.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000 / 30) + offset));
  const h = crypto.createHmac("sha1", key).update(counter).digest();
  const o = h[h.length - 1] & 0xf;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

function latestLink(to, route) {
  const files = fs.readdirSync(OUTBOX).filter((f) => f.endsWith(".eml")).sort().reverse();
  for (const f of files) {
    const raw = fs.readFileSync(path.join(OUTBOX, f), "utf8");
    if (!raw.includes(`To: ${to}`)) continue;
    const m = raw.replace(/=\r?\n/g, "").match(new RegExp(`${route}\\?token=([A-Za-z0-9_-]+)`));
    if (m) return `${route}?token=${m[1]}`;
  }
  throw new Error(`no ${route} mail for ${to}`);
}

async function step(name, fn, page) {
  try {
    await fn();
    steps.push(["PASS", name]);
    if (SHOTS && page) await page.screenshot({ path: path.join(SHOTS, `mfa-${String(steps.length).padStart(2, "0")}-${name}.png`), fullPage: true });
  } catch (e) {
    steps.push(["FAIL", name, String(e.message).split("\n")[0]]);
    if (SHOTS && page) await page.screenshot({ path: path.join(SHOTS, `mfa-FAIL-${name}.png`), fullPage: true });
    throw e;
  }
}

async function signInPassword(page) {
  await page.goto(BASE + "/login");
  await page.getByLabel(/email/i).first().fill(email);
  await page.getByLabel(/^password/i).first().fill(password);
  await page.getByRole("button", { name: /^sign in$/i }).click();
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 } });
const page = await ctx.newPage();
page.on("console", (m) => m.type() === "error" && !/\b(401|400|429)\b/.test(m.text()) && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
let secret = "";
let recovery = [];

try {
  await step("register-verify-login", async () => {
    await page.goto(BASE + "/register");
    await page.getByLabel(/full name|your name|name/i).first().fill("MFA Tester");
    await page.getByLabel(/email/i).first().fill(email);
    await page.getByLabel(/^password/i).first().fill(password);
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: /create workspace/i }).click();
    await page.waitForURL(/verify-email/);
    await page.goto(BASE + latestLink(email, "/verify-email"));
    await page.getByText(/verified/i).first().waitFor();
    await signInPassword(page);
    await page.waitForURL((u) => !u.pathname.startsWith("/login"));
  }, page);

  await step("turn-on-in-settings", async () => {
    await page.goto(BASE + "/settings");
    await page.getByRole("button", { name: /^security$/i }).or(page.getByRole("tab", { name: /^security$/i })).first().click();
    const card = page.getByRole("region", { name: "Two-step sign-in" });
    await card.getByRole("button", { name: /^turn on$/i }).click();
    const dlg = page.getByRole("dialog");
    await dlg.getByLabel(/your password/i).fill(password);
    await dlg.getByRole("button", { name: /^continue$/i }).click();
    await dlg.getByRole("img").first().waitFor(); // QR code
    secret = (await dlg.getByLabel("Setup key").innerText()).replace(/\s+/g, "");
    await dlg.getByLabel(/authentication code/i).fill(totp(secret));
    await dlg.getByRole("button", { name: /^turn on$/i }).click();
    const list = dlg.getByRole("list", { name: "Recovery codes" });
    await list.waitFor();
    recovery = (await list.getByRole("listitem").allInnerTexts()).map((s) => s.trim());
    if (recovery.length !== 10) throw new Error(`expected 10 recovery codes, got ${recovery.length}`);
    const done = dlg.getByRole("button", { name: /^done$/i });
    if (await done.isEnabled()) throw new Error("Done must stay disabled until the codes are saved");
    await dlg.getByRole("checkbox").check();
    await done.click();
    await card.getByText(/recovery codes left/i).waitFor();
  }, page);

  await step("password-alone-is-not-enough", async () => {
    await ctx.clearCookies();
    await signInPassword(page);
    await page.getByLabel(/authentication code|code/i).first().waitFor();
    const me = await page.request.get(BASE + "/api/v1/me");
    if (me.status() !== 401) throw new Error(`expected 401 before the second step, got ${me.status()}`);
  }, page);

  await step("wrong-code-is-refused", async () => {
    await page.getByLabel(/authentication code|code/i).first().fill("000000");
    await page.getByRole("button", { name: /verify and sign in/i }).click();
    await page.getByText(/not correct/i).first().waitFor();
  }, page);

  await step("authenticator-code-signs-in", async () => {
    await page.getByLabel(/authentication code|code/i).first().fill(totp(secret, 1));
    await page.getByRole("button", { name: /verify and sign in/i }).click();
    await page.waitForURL((u) => !u.pathname.startsWith("/login"));
  }, page);

  await step("recovery-code-signs-in", async () => {
    await ctx.clearCookies();
    await signInPassword(page);
    await page.getByRole("button", { name: /use a recovery code/i }).click();
    await page.getByLabel(/authentication code|code/i).first().fill(recovery[0]);
    await page.getByRole("button", { name: /verify and sign in/i }).click();
    await page.waitForURL((u) => !u.pathname.startsWith("/login"));
  }, page);
} catch {
  // reported below
} finally {
  for (const s of steps) console.log(s.join("  "));
  console.log(`console errors (excluding expected 4xx): ${consoleErrors.length}`);
  for (const e of consoleErrors.slice(0, 5)) console.log("  " + e.slice(0, 200));
  await browser.close();
  process.exit(steps.some((s) => s[0] === "FAIL") || steps.length < 6 ? 1 : 0);
}
