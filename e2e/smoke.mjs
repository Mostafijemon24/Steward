// End-to-end smoke test against the REAL backend (not demo mode).
// Prereqs: API on :8000 and web on :3000 (see README "Run locally"), MAIL_OUTBOX_DIR readable.
// Usage: node e2e/smoke.mjs [--shots <dir>]
import { createRequire } from "node:module";
// Uses the globally installed Playwright (or a local one): `npm i -D playwright` if missing.
const { chromium } = createRequire(import.meta.url)("playwright");
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.WEB_URL ?? "http://localhost:3000";
const OUTBOX = process.env.MAIL_OUTBOX_DIR ?? path.resolve("api/var/mail-outbox");
const shotsIdx = process.argv.indexOf("--shots");
const SHOTS = shotsIdx > 0 ? process.argv[shotsIdx + 1] : null;
const email = `e2e-${Date.now()}@example.com`;
const password = "e2e-password-123";
const steps = [];
const consoleErrors = [];

function latestLink(to, route) {
  const files = fs.readdirSync(OUTBOX).filter((f) => f.endsWith(".eml")).sort();
  for (const f of files.reverse()) {
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
    if (SHOTS && page) await page.screenshot({ path: path.join(SHOTS, `${String(steps.length).padStart(2, "0")}-${name.replace(/\W+/g, "-")}.png`), fullPage: true });
  } catch (e) {
    steps.push(["FAIL", name, String(e.message).split("\n")[0]]);
    if (SHOTS && page) await page.screenshot({ path: path.join(SHOTS, `FAIL-${name.replace(/\W+/g, "-")}.png`), fullPage: true });
    throw e;
  }
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 } });
const page = await ctx.newPage();
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e)));
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

try {
  await step("landing", async () => {
    await page.goto(BASE + "/");
    await page.getByRole("heading", { level: 1 }).waitFor();
  }, page);

  await step("register", async () => {
    await page.goto(BASE + "/register");
    await page.getByLabel(/full name|your name|name/i).first().fill("E2E Tester");
    await page.getByLabel(/email/i).first().fill(email);
    await page.getByLabel(/^password/i).first().fill(password);
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: /create workspace/i }).click();
    await page.waitForURL(/verify-email/);
    await page.getByRole("heading", { name: /verify your email/i }).waitFor();
  }, page);

  await step("login-before-verify-is-generic", async () => {
    await page.goto(BASE + "/login");
    await page.getByLabel(/email/i).first().fill(email);
    await page.getByLabel(/^password/i).first().fill(password);
    await page.getByRole("button", { name: /^sign in$/i }).click();
    await page.getByText(/did not match|hasn't been verified/i).first().waitFor();
  }, page);

  await step("verify-email", async () => {
    await page.goto(BASE + latestLink(email, "/verify-email"));
    await page.getByText(/verified/i).first().waitFor();
  }, page);

  await step("login", async () => {
    await page.goto(BASE + "/login");
    await page.getByLabel(/email/i).first().fill(email);
    await page.getByLabel(/^password/i).first().fill(password);
    await page.getByRole("button", { name: /^sign in$/i }).click();
    await page.waitForURL(/onboarding|overview/);
  }, page);

  await step("onboarding", async () => {
    if (!page.url().includes("onboarding")) return;
    for (let i = 0; i < 4; i++) await page.getByRole("button", { name: /^continue$/i }).click();
    await page.getByRole("button", { name: /go to workspace/i }).click();
    await page.waitForURL(/overview/);
  }, page);

  await step("overview", async () => {
    await page.getByRole("heading", { name: /your workspace/i }).waitFor();
    await page.getByText(/not connected/i).first().waitFor();
  }, page);

  let taskUrl = "";
  await step("quick-task-creates-real-task", async () => {
    await page.getByLabel(/describe a task/i).fill("Audit my WordPress website for SEO problems and show fixes first");
    await page.getByRole("button", { name: /plan task/i }).click();
    await page.waitForURL(/\/tasks\/[0-9a-f-]+\/plan/);
    // A plan appears once the worker has planned it (or the page explains it is waiting).
    await page.locator("text=/approve selected steps|waiting|planning/i >> visible=true").first().waitFor();
    taskUrl = page.url().replace(/\/plan$/, "");
  }, page);

  await step("task-detail", async () => {
    await page.goto(taskUrl + "?tab=logs");
    await page.getByText(/queued for planning|plan ready|no approved ai model/i).first().waitFor();
  }, page);

  await step("tasks-list", async () => {
    await page.goto(BASE + "/tasks");
    await page.getByText("T-1001").first().waitFor();
  }, page);

  await step("upload-file", async () => {
    await page.goto(BASE + "/files");
    await page.getByRole("button", { name: /add (a )?file|upload/i }).first().click();
    const input = page.locator('input[type="file"]');
    await input.setInputFiles({ name: "crawl-e2e.csv", mimeType: "text/csv", buffer: Buffer.from("url,status\n/,200\n") });
    await page.getByRole("dialog").getByRole("button", { name: /upload|add file/i }).last().click();
    await page.getByText("crawl-e2e.csv").first().waitFor();
    // "Not scanned" without a scanner; "Scanned — no malware found" when ClamAV is configured.
    await page.locator("text=/not scanned|no malware found/i >> visible=true").first().waitFor();
  }, page);

  await step("file-deletion-request-to-approval", async () => {
    await page.getByText("crawl-e2e.csv").first().click();
    await page.getByRole("button", { name: /request deletion/i }).click();
    await page.getByRole("dialog").last().getByRole("textbox").fill("Not needed after the audit");
    await page.getByRole("dialog").last().getByRole("button", { name: /request deletion|send|submit/i }).last().click();
    await page.waitForURL(/approvals/);
    await page.getByText(/items will be deleted/i).first().waitFor();
  }, page);

  await step("approve-deletion-queues-the-deletion-service", async () => {
    await page.getByRole("button", { name: /approve once/i }).first().click();
    const dlg = page.getByRole("dialog").last();
    for (const cb of await dlg.getByRole("checkbox").all()) await cb.check();
    await dlg.getByRole("button", { name: /approve/i }).last().click();
    await page.locator("text=/waiting to run|used|approved/i >> visible=true").first().waitFor();
  }, page);

  await step("deleted-file-is-restorable", async () => {
    // The deletion service (separate worker) soft-deletes it; it then appears under Recently deleted.
    for (let i = 0; i < 40; i++) {
      const n = await page.evaluate(async () => (await (await fetch("/api/v1/files?deleted=true")).json()).items.length);
      if (n > 0) break;
      await page.waitForTimeout(500);
    }
    await page.goto(BASE + "/files");
    await page.getByRole("button", { name: /recently deleted/i }).first().click();
    await page.getByText("crawl-e2e.csv").first().waitFor();
  }, page);

  await step("integrations-wordpress-is-read-only-without-credentials", async () => {
    await page.goto(BASE + "/integrations");
    await page.getByRole("button", { name: /manage|connect/i }).first().click();
    await page.getByText(/credentials/i).first().waitFor();
    await page.keyboard.press("Escape");
  }, page);

  for (const [route, text] of [
    ["/automations", /automations/i],
    ["/reports", /activity/i],
    ["/security", /security/i],
    ["/settings", /settings/i],
    ["/notifications", /notifications/i],
    ["/help", /help/i],
    ["/states", /system states/i],
  ]) {
    await step(`screen${route.replace("/", "-")}`, async () => {
      await page.goto(BASE + route);
      await page.getByRole("heading", { level: 1, name: text }).first().waitFor();
    }, page);
  }

  await step("activity-shows-audit-events", async () => {
    await page.goto(BASE + "/reports");
    await page.getByText(/signed in/i).first().waitFor();
  }, page);

  await step("mobile-layout", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(BASE + "/overview");
    await page.getByRole("button", { name: /open navigation/i }).click();
    await page.getByRole("dialog", { name: /main navigation/i }).waitFor();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    if (overflow) throw new Error("horizontal page scroll at 390px");
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 1360, height: 900 });
  }, page);

  await step("logout", async () => {
    await page.goto(BASE + "/overview");
    await page.getByRole("button", { name: /account menu/i }).click();
    await page.getByRole("menuitem", { name: /sign out/i }).click();
    await page.waitForURL(/login/);
    await page.goto(BASE + "/overview");
    await page.waitForURL(/login/);
  }, page);
} catch {
  /* reported below */
} finally {
  await browser.close();
  for (const s of steps) console.log(s.join("  "));
  const real = consoleErrors.filter((e) => !/401|Unauthorized|status of 4\d\d/.test(e));
  console.log(`console errors (excluding expected 4xx): ${real.length}`);
  real.slice(0, 10).forEach((e) => console.log("  ", e.slice(0, 300)));
  process.exit(steps.some((s) => s[0] === "FAIL") || real.length ? 1 : 0);
}
