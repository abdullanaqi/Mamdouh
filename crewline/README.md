# Crewline

**An AI-native operating system for commercial cleaning / janitorial contractors.**

Schedule crews across sites, prove the work got done (GPS-verified clock-ins +
photo proof), generate AI quotes, and get paid — built mobile-first for the
field. This repo is the **MVP (spec Phases 0–3)**: the core loop
**Schedule → Perform → Verify → Invoice**, plus the AI layer.

> Phases 4–5 (Twilio SMS agent, Stripe Connect payments) are intentionally
> **not** built — their interfaces are scaffolded and clearly marked deferred.

---

## Stack

- **Next.js 15** (App Router) · **React 19** · **TypeScript** · **Tailwind v4** · shadcn-style UI
- **PostgreSQL** + **Drizzle ORM** (migrations + Row-Level Security)
- **Anthropic Claude** (Sonnet 4.6 / Haiku 4.5) via `@anthropic-ai/sdk`
- **Vitest** for unit/integration tests; a Node HTTP E2E smoke for the core loop
- PWA crew app with an **offline action queue** (IndexedDB) that syncs when back online

See `Crewline_Build_Spec.md` for the full product spec and `AGENTS.md` for the
build operating manual.

---

## Quick start (local)

### 1. Prerequisites
- Node 22+, pnpm 10+
- A PostgreSQL database (local or hosted). Any Postgres 14+ works.

### 2. Install
```bash
pnpm install
```

### 3. Configure environment
```bash
cp .env.example .env
```
Set at minimum:
- `DATABASE_URL` — your Postgres connection string
- `AUTH_SECRET` — `openssl rand -hex 32`
- `NEXT_PUBLIC_APP_URL` — e.g. `http://localhost:3000`

The other variables have safe local defaults (`STORAGE_DRIVER=local`,
`EMAIL_DRIVER=log`, `GEOCODE_DRIVER=manual`). AI features stay dormant until you
set `ANTHROPIC_API_KEY` (see "What needs your keys" below).

### 4. Create the schema + demo data
```bash
pnpm db:migrate   # applies tables + RLS policies
pnpm db:seed      # one demo org with a week of shifts
```

### 5. Run
```bash
pnpm dev          # http://localhost:3000
```

**Demo logins** (after seeding):
- **Owner / admin:** `owner@demo.crewline.app` / `demo1234` → `/dashboard`
- **Crew:** phone `+15555550123` → the dev login screen shows the OTP code
  on-screen (no SMS provider in the MVP).

---

## The core loop, end to end

1. **Owner** signs up (`/signup`) → onboarding wizard → adds a client, a site
   (with checklist + geofence), and invites a cleaner.
2. **Owner** schedules shifts (`/schedule`) — one-off or recurring (RRULE);
   recurring rules materialize concrete shifts.
3. **Crew** opens the PWA (`/today`), taps a shift, **clocks in** (GPS →
   geofence check), works the checklist, uploads proof photos, reports issues,
   **clocks out**. Works offline; actions sync when signal returns.
4. **Owner** opens the shift (`/shifts/[id]`) and sees clock times, geofence
   status, checklist %, photo gallery (with advisory AI flags), and issues.
5. **Owner** generates an **AI quote** (`/quotes/new`), edits the proposal,
   and emails it.
6. **Owner** generates an **invoice** from completed shifts (`/invoices/new`),
   sends it, and marks it paid.

The dashboard (`/dashboard`) rolls this up: today's coverage, outstanding
invoices, open issues, quality flags.

---

## Scripts

| Command | What it does |
|---|---|
| `pnpm dev` | Run the app in dev mode |
| `pnpm build` / `pnpm start` | Production build / serve |
| `pnpm typecheck` | `tsc --noEmit` |
| `pnpm lint` | ESLint (next) |
| `pnpm test` | Vitest unit + integration suite (needs a Postgres test DB) |
| `pnpm db:generate` | Generate a Drizzle migration from `lib/db/schema.ts` |
| `pnpm db:migrate` | Apply migrations + RLS to `DATABASE_URL` |
| `pnpm db:seed` | Reset + seed demo data |
| `pnpm db:reset` | Truncate all tables |

### Running tests
Tests run against a real Postgres (DB-backed tenancy/integration tests). Point
them at a throwaway database via `.env.test` (gitignored) or `DATABASE_URL`:
```bash
# defaults to postgresql://postgres@127.0.0.1:5433/crewline_test
DATABASE_URL=postgres://.../crewline_test pnpm test
```
The suite covers: geofence math, RRULE scheduling + idempotent materialization,
the crew shift flow → owner verification, **tenancy isolation (DAL + Postgres
RLS)**, the AI wrapper (routing, cost, JSON repair/fallback, `ai_runs` logging),
the quote pipeline, invoice math/numbering/lifecycle, the offline queue, and
email rendering.

There is also an HTTP E2E smoke (`tests/e2e/http-smoke.mjs`) that drives the
core loop and cron auth against a running `next start` server.

---

## Multi-tenancy (how isolation is enforced)

Every domain table carries `org_id`. Isolation is enforced in **two layers**:

1. **Data-access layer** — every query in `lib/domain/*` is scoped by `org_id`
   (the runtime guarantee). This is exhaustively tested.
2. **Postgres Row-Level Security** — `lib/db/rls.sql` enables `FORCE ROW LEVEL
   SECURITY` on every tenant table with policies keyed on the
   `app.current_org_id` session GUC, enforced through a non-superuser
   `crewline_app` role. The tenancy test proves cross-org reads/writes are
   blocked at the database itself.

## Auth (MVP note)

The spec calls for Supabase Auth, which requires a hosted Supabase project — a
credential only you can provision. The MVP ships an **equivalent self-contained
session layer** with the same shape: scrypt password login for owners/admins,
stateless phone-OTP for crew, and a signed-JWT httpOnly cookie. The domain layer
is auth-agnostic, so a Supabase adapter can replace `lib/auth/*` without touching
business logic. To go live with Supabase Auth, swap the session/identity
functions and keep the `org_users` membership model.

## Background jobs

Nightly jobs run as cron-guarded API routes (`/api/cron/*`, secured by
`CRON_SECRET`), scheduled via `vercel.json`:
- `materialize` — generate upcoming shifts from recurrence rules (idempotent)
- `mark-missed` — flip past-due scheduled shifts to missed
- `photo-scan` — advisory AI proof-photo quality scan (needs `ANTHROPIC_API_KEY`)
- `invoice-reminders` — mark overdue + email reminders

The job logic lives in `lib/jobs` + `lib/domain` (pure, tested); swapping to
Inngest/Trigger.dev later changes only the transport.

---

## What needs your keys (HANDBACK)

The app runs fully locally with safe driver defaults. These features activate
when you supply real credentials (none can be created by the build agent):

| Capability | Variable(s) | Without it |
|---|---|---|
| AI quotes, proposals, comms, photo/issue AI | `ANTHROPIC_API_KEY` | UI shows "add your key"; enrichments skip silently |
| Real email delivery | `EMAIL_DRIVER=resend` + `RESEND_API_KEY` + `EMAIL_FROM` | `EMAIL_DRIVER=log` records to console, no send |
| Proof-photo cloud storage | `STORAGE_DRIVER=r2` + `R2_*` | `STORAGE_DRIVER=local` writes under `.local-storage` |
| Address geocoding | `GEOCODE_DRIVER=google` + `GOOGLE_MAPS_API_KEY` | `manual` — enter lat/lng on the site form |
| Hosted DB | `DATABASE_URL` (Supabase/Neon) | any local Postgres |

**Deferred (not built — Phases 4–5):** Twilio SMS (`TWILIO_*`) and Stripe
Connect payments (`STRIPE_*`). Their interfaces are scaffolded in
`lib/integrations/{twilio,stripe}.ts` and throw a clear "not implemented" error.

## Deploy

Target is Vercel (Next.js native) + a managed Postgres (Supabase/Neon). Set all
production env vars, run `pnpm db:migrate` against the prod database, and let
`vercel.json` register the cron jobs. Production deploy is a human step (it needs
your Vercel project, domain, and live keys).

---

## Repo layout

```
app/                  # App Router: (marketing) (app) (crew) + api routes + actions
components/            # UI (shadcn-style) + feature components
lib/
  db/                 # Drizzle schema, migrate runner, RLS, seed
  domain/             # business logic (scheduling, shifts, invoicing, quotes…)
  ai/                 # model routing, transport, runAI wrapper, capabilities, prompts
  auth/               # session, password, OTP, org/role context
  integrations/       # storage, email, maps (+ deferred twilio, stripe)
  jobs/               # cron job logic
emails/               # transactional email templates
tests/                # unit + integration + e2e
```
