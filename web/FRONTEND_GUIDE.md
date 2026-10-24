# Frontend guide (for anyone porting or adding screens)

Source of truth for visuals: `../project/Steward AI Agent Workspace.dc.html` (template = lines 29–1502, logic/data = 1503–1909). Recreate each screen's **visual output** faithfully (sizes, colours, spacing, copy) with Tailwind classes. Do not copy the prototype's internal structure.

## Stack
Next.js 16 App Router · React 19 · TypeScript (strict) · Tailwind v4 (tokens in `app/globals.css`) · `@tanstack/react-query` · `lucide-react` via `components/ui/Icon.tsx`.

## Rules
1. **All data goes through `api` from `@/lib/api`** (an `AgentApi`, see `lib/api/client.ts`; wire types in `lib/api/types.ts`). Use react-query: `useQuery({queryKey:[...], queryFn:() => api.x()})`, `useMutation` + `queryClient.invalidateQueries`. Never import `http.ts` or `mock.ts` directly.
2. **Honest states.** Every data view handles loading (`<Skeleton/>`), error (`<ErrorState error onRetry/>`), empty (`<EmptyState/>`). An API error is shown (toast `errorMessage(e)` with tone `danger`, or inline) — never turned into a fake success.
3. **Two modes, one UI.** `DEMO_MODE` (from `@/lib/api`) is true only for the in-browser mock. Copy that says "simulated" belongs to demo mode only. In real mode the backend persists everything, but **the AI engine is not installed (Phase 3)**: tasks stay `queued` with `waiting_for: "planning_service"`, `plan` is `null`, approvals record a decision with `execution_state: "blocked_until_phase3"`. Say so plainly ("Recorded. Nothing ran — the execution engine isn't installed yet.").
4. Labels/colours for enums: `@/lib/labels` (TASK_STATUS, APPROVAL_STATUS, STEP_STATE, FILE_STATUS, CLASSIFICATION, RISK_STEP, RISK_LEVEL, APPROVAL_CATEGORY, INTEGRATION_STATUS, TASK_TYPES, MODES, PRIVACY_MODES, ACTIVITY_*). Dates: `@/lib/format`.
5. UI kit: `@/components/ui` — `Button` (variants primary/secondary/ghost/link/danger; sizes xs=30px, sm=34px, md=38px, lg=44px), `IconButton`, `Badge`, `MonoTag`, `PageHeader`, `Card`, `CardHeader`, `Banner`, `Label`, `TextInput`, `TextArea`, `Select`, `Checkbox`, `Switch`, `Chip`, `Modal`, `Drawer`, `Skeleton`, `EmptyState`, `ErrorState`, `DefList`, `FieldError`, `Icon`. Extend locally in your feature folder if needed; don't fork the kit.
6. App-wide helpers: `useUi()` from `@/lib/session` → `toast(text, tone)`, `notice(title, body)` (the prototype's "backend required" dialog), `compactRows`. `useSession()` → `me`, `setMe`, `refresh`, `logout`.
7. Accessibility from the prototype: visible focus, `aria-label` on icon-only buttons, `role="switch"`, `role="tab"`/`tablist`, `role="radiogroup"`, `role="alert"` on errors, Escape closes overlays (Modal/Drawer already do), status never colour-only (icon + label).
8. Responsive: sidebar ≥ 980px; below, tables become cards (`min-[980px]:` / `max-[979px]:` variants). No horizontal page scroll. Use the prototype's clamp() paddings where present.
9. Pages are client components (`"use client"`) in `features/<area>/…`; the route file in `app/` just renders them. Wrap `useSearchParams` users in `<Suspense>`.
10. No secrets, tokens or form data in `localStorage`. Only UI prefs (`steward.*`).

## Routes
Public: `/` landing · `/register` · `/login` · `/forgot-password` · `/reset-password?token=` · `/verify-email?token=` · `/onboarding`
App (inside `app/(app)/`, wrapped by `AppShell`): `/overview` · `/tasks/new` · `/tasks` · `/tasks/[id]` (`?tab=`) · `/tasks/[id]/plan` · `/approvals` (`?id=&filter=`) · `/files` · `/automations` · `/integrations` · `/reports` · `/security` · `/settings` (`?tab=`) · `/notifications` · `/help` · `/states`
Task "open" rule (prototype `dec().open`): status `awaiting_approval` or `planning` → `/tasks/[id]/plan`, else `/tasks/[id]`.

## Query keys (keep consistent so invalidation works)
`["me"]`, `["mfa"]`, `["dashboard"]`, `["upload-limits"]`, `["tasks", query]`, `["task", id]`, `["task-events", id]`, `["approvals", filter]`, `["approval", id]`, `["files", query]`, `["file", id]`, `["automations"]`, `["integrations"]`, `["reports"]`, `["activity", type]`, `["notifications", filter]`, `["security"]`, `["settings"]`, `["usage"]`.
After any mutation, invalidate the affected keys **and** `["dashboard"]` (sidebar badge, unread count, setup progress read from it).
