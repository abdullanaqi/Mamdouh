# BUILD SPEC — "Crewline"
### An AI-native operating system for commercial cleaning / janitorial contractors
**Version 1.0 · Build-ready handoff for Codex · Mid-2026**

---

## 0. How to use this document (read first)

This is a complete, opinionated build specification. It is written so an autonomous coding agent (Codex) plus a solo founder can go from empty repo to a deployed, paying-customer-ready MVP. It contains:

1. The business decision and why this exact vertical
2. Product definition + MVP scope (what to build, what to *not* build)
3. The full tech stack with versions and rationale
4. System architecture
5. Complete data model (Postgres schema)
6. The AI agent layer (what each agent does, model routing, **actual system prompts**)
7. Third-party integrations (Stripe Connect, Twilio, storage) with exact flows
8. Screen-by-screen MVP UI
9. **Codex build sequence** — phased tickets with acceptance criteria
10. Repo structure + environment variables
11. Go-to-market: pricing, first-100-customers playbook, outreach scripts, landing copy
12. Milestones, metrics, and the decision gates that change the plan

**Working name:** *Crewline*. (Swap freely; the codebase uses `crewline` as the package/namespace.)

> ⚠️ This spec is a starting point, not gospel. The single most important instruction: **get 3–5 real cleaning contractors on the phone before Codex writes the payments module.** Build order assumes you are validating in parallel. See §11.

---

## 1. The decision: why commercial cleaning / janitorial

You chose **Archetype B — an AI-native operating tool for a "boring" trade with embedded payments as Act II.** Among the candidate trades, **commercial cleaning / janitorial** is the highest-probability pick for a solo, near-zero-capital, technical founder. Here's the reasoning, kept tight:

**Why this trade scores highest on the four filters that matter:**

| Filter | Commercial cleaning | Why it wins |
|---|---|---|
| **Still on paper/spreadsheets** | Yes, overwhelmingly | Most small janitorial firms run on group texts, paper checklists, and QuickBooks. Software penetration is low. Blue ocean for a modern tool. |
| **High recurring transaction volume** | Yes — *structurally* | Contracts are recurring (nightly/weekly/monthly). Recurring billing = predictable payment volume = embedded payments (Act II) actually works. This is the single biggest reason to pick cleaning over, say, one-off handyman work. |
| **Fragmented, reachable buyers** | Yes | Hundreds of thousands of small operators in the US; owner-operators are findable on Google Maps, Facebook groups, and trade associations (ISSA, BSCAI). Low CAC via SEO + direct outreach. |
| **Deep enough for $100M+ ARR** | Yes | The janitorial services market is tens of billions; even a small software+payments take rate on a sliver of it is a very large business. |

**The specific, painful, budgeted problems** (this is what you sell against):

1. **"Did my crew actually show up and do the work?"** — Owners can't verify service across distributed sites. Lost contracts come from unverified, inconsistent quality.
2. **Scheduling crews across many sites** — done in spreadsheets and texts; brittle, error-prone, no coverage when someone calls out.
3. **Quoting new jobs is slow and inconsistent** — owners eyeball a walkthrough and guess; inconsistent pricing kills margin.
4. **Getting paid** — invoicing is manual, collections lag, cash flow suffers. (This is the wedge for embedded payments.)
5. **Client communication** — issue reports, requests, and "you missed the trash on the 3rd floor" live in scattered texts and emails.

**The AI-native angle (why this is a 2026 company, not a 2015 FieldService clone):** AI lets a *single founder* ship capabilities that previously needed a team — automated quote generation from a site walkthrough, AI quality verification from crew photos, an AI agent that handles inbound client requests and crew check-ins over SMS, and AI-drafted client communications. The legacy incumbents (Jobber, ServiceTitan-for-trades, Aspire) are horizontal or up-market and slow; the opening is an **AI-first, cleaning-specific, mobile-first tool for the 1–50 employee operator.**

**The moat you will build (per the research):**
- **Proprietary data flywheel:** every shift, photo, checklist, and payment becomes data on what good service looks like, which crews perform, and client payment behavior. Competitors can't replicate it.
- **Workflow embeddedness / system of record:** once scheduling + proof-of-work + invoicing + payments run through Crewline, ripping it out breaks the business.
- **Embedded payments lock-in (Act II):** once client money flows through you, switching means re-plumbing cash flow. This is also the 2–5× ARPU multiplier that turns a "small" niche into a large company.

**Honest framing (do not skip):** The modal outcome for this is a $1–10M ARR business. A great outcome is a category-leading $50M–$1B+ platform reached over many years, almost certainly with a team and outside capital by the later stages. A clean "$1T solo" outcome is not a plan; maximizing the probability of a billions-scale outcome is. Build accordingly: nail one workflow, get to recurring revenue, then layer payments.

---

## 2. Product definition & MVP scope

### 2.1 One-sentence definition
**Crewline is the mobile-first operating system that lets a commercial cleaning contractor schedule crews across all their sites, prove the work got done, communicate with clients, and get paid — with AI doing the quoting, quality-checking, and client comms.**

### 2.2 Primary user (be specific)
**"Independent commercial cleaning companies, 1–50 cleaners, that service recurring B2B contracts (offices, medical, retail, schools) and currently run on spreadsheets, group texts, and QuickBooks."**

Two user roles in the MVP:
- **Owner/Admin** (desktop + mobile): sets up sites, schedules, sees dashboards, sends invoices, manages clients.
- **Cleaner/Crew** (mobile only): sees their shifts, clocks in/out with geofence, completes the checklist, uploads proof photos, reports issues.

(A third role — **Client** — gets a lightweight portal/links in v1.1, not v1. See phasing.)

### 2.3 MVP scope — the "ONE workflow" done deeply
The research is emphatic: ship a thin, opinionated v1 that does **one workflow** extremely well, then expand. For Crewline, the **one workflow is "Schedule → Perform → Verify → Invoice."** Everything in v1 serves that loop.

**IN SCOPE for v1 (MVP):**
1. **Auth & org setup** — owner signs up, creates the company (tenant), invites cleaners.
2. **Sites & clients** — CRUD for client accounts and the sites/locations under them, with per-site checklists.
3. **Scheduling** — create recurring and one-off shifts; assign crew; calendar + list views; handle call-outs (reassign).
4. **Mobile crew app (PWA)** — cleaner sees today's shifts, clocks in/out (with GPS geofence verification), works the checklist, uploads proof photos, reports an issue.
5. **Proof-of-work / quality** — owner sees completed shifts with photos, checklist completion, and AI quality flags.
6. **AI quote generator** — owner inputs site details (size, type, frequency, scope) or pastes an RFP/walkthrough notes; AI produces a priced, formatted quote/proposal.
7. **Invoicing** — generate invoices from completed shifts/contracts; send to client; mark paid (manual in v1, Stripe in v1.2).
8. **AI client-comms assistant** — drafts replies to client messages/issues; drafts the cover note on quotes and invoices.
9. **Owner dashboard** — today's coverage, shifts completed/missed, outstanding invoices, flagged quality issues.

**EXPLICITLY OUT OF SCOPE for v1** (resist building these; note them as backlog):
- Embedded payments / Stripe Connect (this is **Act II — v1.2**, after retention is proven).
- Full client self-service portal (v1.1).
- Payroll / timesheet export to ADP etc. (v1.1 — but capture the clock data now).
- Inventory/supplies tracking.
- Native iOS/Android apps (PWA only in v1).
- Multi-language UI (English only v1; Spanish is a fast-follow given the workforce — v1.1).
- Advanced route optimization.
- Bidding marketplace / lead gen.

### 2.4 The "Clone Test" features (what makes it defensible from day one)
Build these into the data model now even if simple in v1, because they compound:
- Every shift stores structured outcome data (on-time, checklist %, photos, issues).
- Every quote stores inputs + final price + win/loss (training data for pricing intelligence).
- Every client interaction is logged and structured.

---

## 3. Tech stack (specific, current as of mid-2026)

Optimize for: **one technical founder + Codex velocity, low cost, boring/proven, AI-native.**

### 3.1 Core
- **Language/runtime:** TypeScript everywhere. Node 22 LTS.
- **Framework:** **Next.js 15 (App Router)** — single codebase for marketing site, web app, API routes, and the PWA. Server Components + Server Actions reduce glue code.
- **UI:** **React 19**, **Tailwind CSS v4**, **shadcn/ui** component library (Radix under the hood). Mobile-first.
- **PWA:** Next.js PWA config (installable, offline-tolerant for the crew app). Use `next-pwa` or a service worker; cache the crew "today" view so it works on bad job-site signal.
- **State/data fetching:** TanStack Query for client cache; Server Actions for mutations.
- **Forms/validation:** React Hook Form + **Zod** (Zod schemas shared client/server and reused for AI tool I/O — see §6).

### 3.2 Backend & data
- **Database:** **PostgreSQL** (managed). Use **Supabase** or **Neon** to start (cheap, fast, generous free tiers). Supabase also gives you auth + storage + row-level security in one, which is ideal for a solo founder.
- **ORM:** **Drizzle ORM** (type-safe, lightweight, great migrations) — or Prisma if Codex prefers; spec uses Drizzle.
- **Auth:** **Supabase Auth** (email magic link + password; phone OTP for crew). Multi-tenant via `org_id` on every row + Postgres Row-Level Security policies. (If not using Supabase, use **Auth.js / Clerk**.)
- **File/object storage:** **Supabase Storage** or **Cloudflare R2** (cheap egress) for proof photos. Store keys/URLs in DB; never the binary.
- **Background jobs / scheduling:** **Inngest** or **Trigger.dev** for recurring shift generation, invoice reminders, and async AI tasks (quote generation, nightly quality scans). Avoid standing up your own queue infra in v1.

### 3.3 AI layer
- **Provider:** Anthropic Claude API (Messages API).
- **Models & routing (verified current pricing, mid-2026):**
  - **Claude Haiku 4.5** (`claude-haiku-4-5`) — $1 / $5 per 1M tokens. Use for high-volume, cheap tasks: classification, checklist/issue parsing, photo-caption checks, SMS intent routing.
  - **Claude Sonnet 4.6** (`claude-sonnet-4-6`) — $3 / $15 per 1M tokens. The default workhorse: quote generation, proposal writing, client-comms drafting, structured reasoning.
  - **(Optional) Claude Opus 4.8** (`claude-opus-4-8`) — $5 / $25 — reserve for the hardest reasoning only if Sonnet underperforms; usually unnecessary in v1.
  - **Cost controls:** enable **prompt caching** (up to ~90% off cached input — cache your big system prompts/templates) and **batch processing** (50% off) for non-urgent nightly jobs. Buffer token COGS ~3× when pricing agent features.
- **Vision:** Sonnet 4.6 multimodal for proof-photo quality checks (e.g., "does this photo show an emptied trash can / clean restroom per the checklist item?").
- **SDK:** official `@anthropic-ai/sdk`.
- **Pattern:** define every AI capability as a typed function with a Zod-validated input/output. Force structured JSON output where you parse it (instruct "respond only with JSON, no preamble or markdown," then parse defensively). See §6.

### 3.4 Integrations (added in later phases, scaffold interfaces now)
- **Payments (v1.2):** **Stripe Connect** (embedded payments — see §7.1).
- **Messaging (v1.1):** **Twilio** Programmable SMS (+ optional Voice) for crew check-in reminders, client notifications, and the SMS AI agent. Use **Twilio Verify** for crew phone OTP.
- **Email:** **Resend** (transactional: invoices, quotes, magic links) with React Email templates.
- **Maps/geocoding:** Google Maps Platform or Mapbox for site addresses + geofence radius + (later) routing.

### 3.5 Infra / ops
- **Hosting:** **Vercel** (Next.js native, zero-config, scales to zero — ideal cost profile). DB on Supabase/Neon.
- **Error monitoring:** Sentry. **Product analytics:** PostHog (also gives session replay + feature flags, useful solo).
- **CI:** GitHub Actions (typecheck, lint, test, drizzle migration check on PR).
- **Secrets:** Vercel env vars + a local `.env` (see §10.3). Never commit secrets.

### 3.6 Estimated run cost at MVP / early stage
Roughly **$3K–$12K/year** all-in until you have meaningful volume (Vercel + Supabase/Neon + Anthropic usage + Twilio + Resend + Sentry/PostHog). Matches the research's solo-stack economics. AI cost per quote is cents with caching; per active org per month is low single-digit dollars early.

---

## 4. System architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                            CLIENTS                                      │
│                                                                        │
│   Marketing site        Owner Web App         Crew PWA (mobile)        │
│   (Next.js pages)       (Next.js App)         (Next.js, installable)   │
└───────────────┬───────────────┬───────────────────┬───────────────────┘
                │               │                   │
                ▼               ▼                   ▼
        ┌───────────────────────────────────────────────────┐
        │            Next.js (Vercel) — App Router           │
        │   Server Components · Server Actions · API Routes  │
        │   ┌─────────────┐  ┌──────────────┐  ┌──────────┐  │
        │   │ Auth guard  │  │ Domain logic │  │ AI layer │  │
        │   │ (org_id +   │  │ (scheduling, │  │ (agents, │  │
        │   │  RLS)       │  │  invoicing)  │  │  prompts)│  │
        │   └─────────────┘  └──────────────┘  └────┬─────┘  │
        └───────┬───────────────────┬────────────────┬───────┘
                │                   │                │
                ▼                   ▼                ▼
        ┌──────────────┐   ┌────────────────┐  ┌──────────────────┐
        │  PostgreSQL  │   │  Object store  │  │  Anthropic API   │
        │ (Supabase/   │   │ (proof photos: │  │  Sonnet 4.6 /    │
        │  Neon) +RLS  │   │  Supabase/R2)  │  │  Haiku 4.5       │
        └──────────────┘   └────────────────┘  └──────────────────┘
                │
                ▼
        ┌─────────────────────────────────────────────────────────┐
        │  Background jobs (Inngest/Trigger.dev):                  │
        │  • generate recurring shifts nightly                     │
        │  • invoice + payment reminders                           │
        │  • nightly AI quality scan of the day's proof photos     │
        └─────────────────────────────────────────────────────────┘

        Later phases (scaffold interfaces now, implement in v1.1/v1.2):
        • Twilio (SMS/Voice agent, OTP, notifications)
        • Stripe Connect (embedded payments)  ← Act II / the ARPU multiplier
        • Resend (email)  • Maps (geocode + geofence)
```

**Key architectural principles for Codex:**
- **Multi-tenant from line one.** Every domain table has `org_id`. Every query is scoped by `org_id`. Enforce with Postgres RLS *and* in the data-access layer. This is non-negotiable; retrofitting tenancy is painful.
- **Thin controllers, fat domain layer.** Business logic in `/lib/domain/*`, not in route handlers, so it's testable and reusable from Server Actions, API routes, and jobs.
- **AI is a service, not sprinkled everywhere.** All model calls go through `/lib/ai/*` with typed inputs/outputs, centralized prompt templates, model routing, caching, and logging. Makes cost control and prompt iteration sane.
- **Offline-tolerant crew app.** The crew "today" view and clock-in/checklist must queue actions locally and sync when signal returns (job sites have bad connectivity).

---

## 5. Data model (PostgreSQL / Drizzle)

Below is the v1 schema. Types are Postgres; Drizzle definitions follow the same shape. All timestamps are `timestamptz`. All tables (except `orgs` and `users`) carry `org_id` for tenancy. Use UUID primary keys (`gen_random_uuid()`).

### 5.1 Entity overview
```
orgs ──< users (membership via org_users)
orgs ──< clients ──< sites ──< site_checklist_items
orgs ──< shifts (FK: site_id, assigned_user_id) ──< shift_checklist_results
                                                 └──< shift_photos
                                                 └──< issues
orgs ──< quotes (FK: client_id?, site_id?)
orgs ──< invoices (FK: client_id, site_id?) ──< invoice_line_items
orgs ──< messages (client comms log)
orgs ──< ai_runs (audit/log of every AI call)
```

### 5.2 Tables

**orgs** — the tenant (a cleaning company)
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| name | text | company name |
| timezone | text | e.g. "America/Chicago"; drives scheduling |
| plan | text | 'starter' \| 'pro' \| 'scale' (billing tier) |
| created_at | timestamptz | |

**users** — a person (owner or cleaner)
| column | type | notes |
|---|---|---|
| id | uuid PK | mirrors Supabase auth user id |
| email | text | nullable for crew who only use phone |
| phone | text | E.164; used for crew OTP + SMS |
| full_name | text | |
| created_at | timestamptz | |

**org_users** — membership + role (many-to-many)
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK→orgs | |
| user_id | uuid FK→users | |
| role | text | 'owner' \| 'admin' \| 'cleaner' |
| status | text | 'active' \| 'invited' \| 'disabled' |
| hourly_rate_cents | int | optional, for later payroll/cost |
| created_at | timestamptz | |

**clients** — the cleaning company's customers (the businesses they clean for)
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| name | text | e.g. "Acme Dental Group" |
| contact_name | text | |
| contact_email | text | |
| contact_phone | text | |
| billing_terms | text | 'net15' \| 'net30' \| 'due_on_receipt' |
| notes | text | |
| created_at | timestamptz | |

**sites** — a physical location under a client
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| client_id | uuid FK→clients | |
| name | text | e.g. "Acme Dental — North Office" |
| address | text | |
| lat | double precision | for geofence |
| lng | double precision | for geofence |
| geofence_radius_m | int | default 150 |
| site_type | text | 'office' \| 'medical' \| 'retail' \| 'school' \| 'industrial' \| 'other' |
| square_footage | int | nullable; used by quote AI |
| service_frequency | text | 'daily' \| 'weekly' \| 'biweekly' \| 'monthly' \| 'custom' |
| contract_rate_cents | int | recurring price per service (for invoicing) |
| active | boolean | |
| created_at | timestamptz | |

**site_checklist_items** — the template tasks for a site
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| site_id | uuid FK→sites | |
| label | text | e.g. "Empty all trash bins" |
| area | text | e.g. "Restrooms", "Lobby" |
| requires_photo | boolean | if true, crew must upload proof |
| sort_order | int | |

**shifts** — a scheduled instance of cleaning a site
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| site_id | uuid FK→sites | |
| assigned_user_id | uuid FK→users | nullable (unassigned) |
| scheduled_start | timestamptz | |
| scheduled_end | timestamptz | |
| status | text | 'scheduled' \| 'in_progress' \| 'completed' \| 'missed' \| 'canceled' |
| clock_in_at | timestamptz | nullable |
| clock_in_lat | double precision | nullable |
| clock_in_lng | double precision | nullable |
| clock_in_within_geofence | boolean | computed at clock-in |
| clock_out_at | timestamptz | nullable |
| recurrence_id | uuid | nullable; groups shifts generated from one rule |
| ai_quality_score | int | nullable 0–100, set by nightly AI scan |
| ai_quality_notes | text | nullable |
| created_at | timestamptz | |

**recurrences** — the rule that generates shifts (optional table; or store rule on site)
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| site_id | uuid FK | |
| rrule | text | iCal RRULE string |
| default_assigned_user_id | uuid | nullable |
| start_time_local | text | "18:00" |
| duration_minutes | int | |
| active | boolean | |

**shift_checklist_results** — what got done on a specific shift
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| shift_id | uuid FK→shifts | |
| checklist_item_id | uuid FK→site_checklist_items | snapshot label too |
| label_snapshot | text | label at time of shift |
| completed | boolean | |
| completed_at | timestamptz | |
| photo_id | uuid FK→shift_photos | nullable |

**shift_photos** — proof photos
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| shift_id | uuid FK→shifts | |
| storage_key | text | object-store key |
| url | text | signed/public URL |
| caption | text | crew-entered or AI-generated |
| ai_pass | boolean | nullable; AI vision check result |
| ai_reason | text | nullable |
| created_at | timestamptz | |

**issues** — problems reported by crew or client
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| site_id | uuid FK | |
| shift_id | uuid FK | nullable |
| reported_by_user_id | uuid FK | nullable (client-reported = null) |
| source | text | 'crew' \| 'client' \| 'ai' |
| severity | text | 'low' \| 'medium' \| 'high' |
| status | text | 'open' \| 'acknowledged' \| 'resolved' |
| description | text | |
| ai_summary | text | nullable |
| created_at | timestamptz | |

**quotes** — AI-generated proposals
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| client_id | uuid FK | nullable (prospect) |
| site_id | uuid FK | nullable |
| prospect_name | text | for not-yet-clients |
| inputs_json | jsonb | structured inputs to the quote AI |
| line_items_json | jsonb | AI output: itemized scope + prices |
| total_cents | int | |
| frequency | text | matches site frequency options |
| status | text | 'draft' \| 'sent' \| 'won' \| 'lost' |
| proposal_text | text | AI-written proposal body |
| created_at | timestamptz | |

**invoices**
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| client_id | uuid FK | |
| site_id | uuid FK | nullable |
| number | text | human-friendly, per-org sequence |
| period_start | date | |
| period_end | date | |
| subtotal_cents | int | |
| tax_cents | int | |
| total_cents | int | |
| status | text | 'draft' \| 'sent' \| 'paid' \| 'overdue' \| 'void' |
| due_date | date | |
| sent_at | timestamptz | nullable |
| paid_at | timestamptz | nullable |
| stripe_payment_intent_id | text | nullable (v1.2) |
| created_at | timestamptz | |

**invoice_line_items**
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| invoice_id | uuid FK→invoices | |
| description | text | |
| quantity | numeric | |
| unit_price_cents | int | |
| amount_cents | int | |

**messages** — client communication log (and AI drafts)
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| client_id | uuid FK | nullable |
| site_id | uuid FK | nullable |
| direction | text | 'inbound' \| 'outbound' |
| channel | text | 'sms' \| 'email' \| 'internal' |
| body | text | |
| ai_generated | boolean | |
| status | text | 'draft' \| 'sent' \| 'received' |
| created_at | timestamptz | |

**ai_runs** — audit log of every AI call (cost + quality tracking; also your data moat)
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| capability | text | 'quote' \| 'proposal' \| 'comms_draft' \| 'photo_check' \| 'issue_triage' \| 'sms_agent' |
| model | text | model id used |
| input_tokens | int | |
| output_tokens | int | |
| cost_cents | numeric | computed |
| latency_ms | int | |
| input_ref | jsonb | redacted/structured input |
| output_ref | jsonb | structured output |
| created_at | timestamptz | |

**stripe_accounts** (v1.2 — scaffold now, populate later)
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK | |
| stripe_connected_account_id | text | acct_... |
| charges_enabled | boolean | |
| payouts_enabled | boolean | |
| onboarding_status | text | |
| created_at | timestamptz | |

### 5.3 Indexes & constraints (don't forget)
- Index `shifts (org_id, scheduled_start)`, `shifts (org_id, status)`, `shifts (assigned_user_id, scheduled_start)`.
- Index `invoices (org_id, status, due_date)`.
- Unique `invoices (org_id, number)`.
- Unique `org_users (org_id, user_id)`.
- FK `on delete` rules: cascade children of a deleted org; restrict deleting a client with active sites/invoices.
- RLS policies: a user can only read/write rows where `org_id` ∈ their active memberships; cleaners are further restricted to their own assigned shifts for write operations.

---

## 6. The AI agent layer

This is what makes Crewline AI-native. Each capability is a typed function in `/lib/ai/`. **Centralize**: one client wrapper, one place for model routing, prompt templates, caching, JSON parsing, and `ai_runs` logging.

### 6.1 Wrapper contract (pseudocode)
```ts
// /lib/ai/client.ts
type Capability =
  | "quote" | "proposal" | "comms_draft"
  | "photo_check" | "issue_triage" | "sms_agent";

const MODEL_ROUTING: Record<Capability, string> = {
  quote:        "claude-sonnet-4-6",
  proposal:     "claude-sonnet-4-6",
  comms_draft:  "claude-sonnet-4-6",
  issue_triage: "claude-haiku-4-5",
  photo_check:  "claude-sonnet-4-6", // vision
  sms_agent:    "claude-haiku-4-5",  // fast intent routing; escalate to sonnet if needed
};

// runAI(capability, { system, messages, schema }) ->
//   - injects cached system prompt (prompt caching on the big static parts)
//   - calls Anthropic Messages API with routed model
//   - if schema provided: instruct JSON-only, parse with Zod, repair-on-fail (one retry)
//   - logs tokens/cost/latency to ai_runs
//   - returns typed result
```

**Always:** set `max_tokens` sensibly per capability; enable prompt caching (`cache_control: { type: "ephemeral" }`) on the static system/template prefix; log every call; never block the request path on a slow call — long generations (quotes/proposals) run in a job and notify when ready, or stream.

### 6.2 Capability specs + system prompts

> The prompts below are production-grade starting points. Keep the static instructional part stable (so prompt caching works) and pass the variable data as the user message.

---

#### (A) Quote generator — `capability: "quote"` (Sonnet 4.6)
**Job:** Turn site details (or a pasted RFP/walkthrough notes) into an itemized, priced cleaning quote.

**Input (Zod):**
```ts
{
  siteType: "office"|"medical"|"retail"|"school"|"industrial"|"other",
  squareFootage?: number,
  frequency: "daily"|"weekly"|"biweekly"|"monthly"|"custom",
  scopeNotes: string,            // free text from owner or pasted RFP
  region?: string,               // for labor-cost context
  ownerPricingHints?: string,    // e.g. "we charge ~$0.10/sqft for offices"
  pastQuotes?: Array<{inputs:any,total:number,won:boolean}> // few-shot from this org's history (the data moat!)
}
```
**Output (Zod):**
```ts
{
  lineItems: Array<{ description: string; area?: string; unitBasis: string; quantity: number; unitPriceCents: number; amountCents: number }>,
  totalCents: number,
  assumptions: string[],
  confidence: "low"|"medium"|"high"
}
```

**System prompt:**
```
You are an expert commercial-cleaning estimator helping a janitorial company owner produce an accurate, professional, itemized quote. You understand how cleaning jobs are scoped and priced: by square footage, by fixture/area counts, by frequency, and by site type (medical and industrial cost more per square foot than standard offices; higher frequency lowers per-visit cost but raises monthly totals).

Rules:
- Produce a realistic, itemized quote. Break the job into line items by task/area (e.g., restrooms, floors, trash, common areas, disinfection).
- Use any pricing hints or this company's past won/lost quotes provided to calibrate price. If past quotes are provided, weight them heavily — they reflect this company's market and cost structure.
- If critical inputs are missing (e.g., square footage), state a clear assumption in "assumptions" rather than refusing.
- Be specific with quantities and unit bases (per sq ft, per restroom, per visit, per month).
- Prices are in integer cents. Do the arithmetic carefully so line items sum to the total.
- Never invent regulatory claims. Keep it grounded and professional.
- Set "confidence" based on how complete the inputs are.

Respond ONLY with a single JSON object matching the requested schema. No preamble, no markdown, no code fences.
```

---

#### (B) Proposal writer — `capability: "proposal"` (Sonnet 4.6)
**Job:** Turn the accepted quote into a polished, client-ready proposal/cover note.

**System prompt:**
```
You write concise, persuasive, professional cleaning-service proposals for a janitorial company. Given the company name, the prospective client, the site, and an itemized quote, write a proposal that:
- Opens with a brief, warm, confident introduction tailored to the client and site type.
- Summarizes the scope of work clearly (group the line items into readable sections).
- States the price and frequency plainly and the value behind it (reliability, vetted crews, verified service, responsive communication).
- Closes with a clear call to action and next steps.
- Is honest and grounded — no exaggerated guarantees, no invented credentials.
- Tone: professional, direct, friendly. Length: tight (the client is busy). Use clean formatting with short paragraphs and, where helpful, a short bulleted scope list.

Write the proposal body text only (the structured quote is rendered separately). Do not fabricate certifications, insurance details, or client references; if the owner wants those included, they will be provided in the input.
```

---

#### (C) Client-comms assistant — `capability: "comms_draft"` (Sonnet 4.6)
**Job:** Draft replies to client messages and issue responses in the owner's voice.

**System prompt:**
```
You are the communications assistant for a commercial cleaning company owner. You draft replies to clients that are professional, accountable, and solution-oriented. Given the conversation context, the client, the site, and any related issue or shift data, draft a reply that:
- Directly addresses the client's message or complaint.
- Takes appropriate ownership without over-apologizing or admitting fault not in evidence.
- States concretely what will be done and by when, using the real shift/issue/site data provided.
- Is warm, brief, and clear.

Never invent facts (dates, names, actions taken). Use only the data provided; if a detail is unknown, write a neutral placeholder in [brackets] for the owner to fill. Output the draft message text only.
```

---

#### (D) Proof-photo quality check — `capability: "photo_check"` (Sonnet 4.6, vision)
**Job:** For a checklist item that requires a photo, judge whether the photo plausibly shows the task done.

**Input:** the image + the checklist item label + site type.
**Output (Zod):** `{ pass: boolean, reason: string, confidence: "low"|"medium"|"high" }`

**System prompt:**
```
You are a cleaning quality inspector reviewing a single proof photo submitted by a cleaner against one specific checklist task. You will be given the task label (e.g., "Empty all trash bins", "Clean and disinfect restroom sinks") and the photo.

Judge ONLY whether the photo plausibly shows that this specific task was completed to a reasonable commercial-cleaning standard. Be fair and practical — you are checking for obvious problems (full trash can, visibly dirty surface, wrong area entirely), not nitpicking lighting or framing.

Output a JSON object: { "pass": boolean, "reason": short string, "confidence": "low"|"medium"|"high" }.
- pass=true if the photo reasonably supports task completion.
- pass=false if it clearly does not, or shows the opposite of done.
- If the photo is ambiguous or doesn't clearly show the relevant area, pass=true with confidence "low" and a note (don't penalize crews for imperfect photos).
Respond ONLY with the JSON object. No markdown, no extra text.
```
> Product rule: AI photo checks are **advisory** — they flag for owner review, never auto-penalize a crew. This protects trust and avoids false-negative blowback.

---

#### (E) Issue triage — `capability: "issue_triage"` (Haiku 4.5)
**Job:** Parse a free-text issue (from crew or client) into structured severity + summary + suggested action.

**Output (Zod):** `{ severity: "low"|"medium"|"high", summary: string, suggestedAction: string }`

**System prompt:**
```
You triage operational issues for a commercial cleaning company. Given a free-text issue report and minimal context (site, source), classify it.

Output JSON: { "severity": "low"|"medium"|"high", "summary": one-sentence neutral summary, "suggestedAction": one concrete next step for the owner }.
Severity guide: high = client-facing complaint, safety, security, or risk of losing the contract; medium = service miss needing prompt fix; low = minor or informational. Be concise and practical. Respond ONLY with the JSON object.
```

---

#### (F) SMS agent — `capability: "sms_agent"` (Haiku 4.5, escalate to Sonnet) — *v1.1*
**Job:** Handle inbound SMS from crews ("running 15 late", "can't get in, door locked") and simple client texts, routing to the right action or escalating to the owner.

**System prompt (sketch — implement in v1.1):**
```
You are the SMS assistant for a commercial cleaning company. You handle short text messages from cleaners and clients. Identify intent (running late, cannot access site, sick/callout, job done, client request, complaint, other) and respond briefly and helpfully. For anything involving a complaint, access failure, or callout, take the structured action (log issue, notify owner, attempt reassignment) and tell the sender what will happen. Never make promises you can't keep. Keep replies under 320 characters. When unsure, escalate to the owner and say so.
```

### 6.3 AI guardrails (apply across all capabilities)
- **Structured output discipline:** for any parsed output, instruct JSON-only and validate with Zod; on parse failure, do exactly one repair retry, then fall back to a safe default + flag for human.
- **No fabrication:** prompts forbid inventing facts (dates, credentials, actions). Comms drafts use `[brackets]` placeholders for unknowns.
- **Human-in-the-loop where it matters:** quotes, proposals, and client messages are **drafts the owner approves**, not auto-sent, in v1. Photo checks are advisory.
- **Cost logging:** every call writes to `ai_runs`. Build a tiny internal "AI cost per org" view early so you can price correctly.
- **Caching:** static system prompts + templates use prompt caching; nightly photo scans use the Batch API (50% off).

---

## 7. Integrations (exact flows)

### 7.1 Stripe Connect — embedded payments (Act II, v1.2)
**This is the ARPU multiplier and the deep moat. Do NOT build it in v1.** Add it once you have retained, paying SaaS customers asking to collect payments through Crewline.

**Model:** Crewline is a **platform**; each cleaning company is a **connected account**; the cleaning company's clients pay invoices, funds route to the cleaning company, and Crewline takes an **application fee** on each transaction (this is your payments revenue on top of subscription).

**Account type:** Use **Stripe Connect with embedded onboarding components** (Express-style) so contractors onboard in ~10–15 min with Stripe handling KYC, compliance, payouts, and 1099s. You stay PCI-light. (Verified: Stripe Connect handles onboarding, verification, KYC, payouts in 50+ countries, and tax/1099 automation; "go live in weeks instead of quarters.")

**Pricing/fees (verified, mid-2026):** Standard US online card processing commonly listed at **2.9% + $0.30** per transaction. As the platform you are responsible for Stripe fees and **collect an application fee** to earn revenue (and ensure your platform balance stays positive). Use the **platform pricing tool** to set application-fee rules without custom code. Decide whether you absorb Stripe's fee and bill connected accounts, or pass it through and add your margin on top.

**Core flow (destination charges):**
1. **Onboard:** owner clicks "Set up payments" → create connected account via the accounts API → render Stripe embedded onboarding component → store `stripe_connected_account_id`, poll `charges_enabled`/`payouts_enabled` into `stripe_accounts`.
2. **Pay an invoice:** when a client pays a Crewline invoice, create a **PaymentIntent** with `transfer_data.destination = <connected account>` and `application_fee_amount = <your fee in cents>`. Store `stripe_payment_intent_id` on the invoice.
3. **Pre-verification edge case:** if charges arrive before the account is fully verified, hold funds on unverified charges, then switch to destination charges with `transfer_data.destination` once verified.
4. **Webhooks:** handle `payment_intent.succeeded` (mark invoice paid, set `paid_at`), `account.updated` (update capabilities), `charge.dispute.created` (flag), `payout.*` (optional reporting). Verify webhook signatures.
5. **Reconciliation:** surface payment status on the invoice; use Connect reporting for the owner's payout view.

**Pricing strategy for your take rate:** start with a modest application fee (e.g., a small % per transaction) layered on top of subscription. The research shows embedded payments can lift revenue per customer 2–5×; this is how a "small" niche becomes a multi-billion-dollar TAM. Keep it transparent to the contractor.

### 7.2 Twilio — SMS/Voice (v1.1)
- **Crew OTP:** Twilio Verify for phone-based crew login.
- **Notifications:** shift reminders, "you're not clocked in" nudges, client "crew is on the way / done" texts.
- **Inbound SMS agent:** webhook → `sms_agent` capability (§6.2F) → action/escalation. Log to `messages`.
- Provision a number per org or a shared number with org routing; respect opt-in/opt-out (STOP/HELP) compliance.

### 7.3 Resend — email (v1)
- Transactional: magic-link/login, invoice delivery (PDF link), quote/proposal delivery, invoice reminders.
- React Email templates; from a verified domain; include unsubscribe where required.

### 7.4 Storage — proof photos (v1)
- Direct-to-storage upload from the crew PWA (signed upload URL) → store `storage_key` + signed `url` in `shift_photos`. Compress client-side before upload (job-site bandwidth). Generate thumbnails for the owner gallery.

### 7.5 Maps — geocoding + geofence (v1)
- Geocode site address on create → store lat/lng. At clock-in, compute distance from site; set `clock_in_within_geofence`. (Haversine in app; no need for heavy routing in v1.)

---

## 8. Screen-by-screen MVP UI

### 8.1 Marketing site (public)
- **Landing page** (copy in §11.4), pricing page, "book a demo"/signup CTA, simple SEO content hub (blog) scaffolded for the content engine.

### 8.2 Owner web app (desktop-first, responsive)
1. **Onboarding wizard** — create org (name, timezone), add first client, add first site (+ checklist), invite first cleaner. Goal: time-to-first-value < 10 min.
2. **Dashboard** — today's coverage (shifts scheduled / in-progress / completed / missed), outstanding invoices total, open issues, quality flags. The "is my business OK right now?" screen.
3. **Schedule** — calendar + list; create/edit shifts; recurring shift rules; drag-to-reassign; "uncovered shift" alerts.
4. **Clients & Sites** — list/detail; site detail holds checklist editor, contract rate, frequency, geofence.
5. **Shift detail (verification view)** — per completed shift: clock in/out times, geofence status, checklist completion %, proof photos gallery with AI flags, issues.
6. **Quotes** — "New quote" form → AI generates itemized quote + proposal draft → owner edits → send (email) → track status (won/lost feeds the data moat).
7. **Invoices** — generate from completed shifts/contracts → line items → send → status; (v1.2: "Pay now" via Stripe).
8. **Messages/Issues** — inbox of client messages + issues; AI-drafted replies the owner approves/sends.
9. **Settings** — org, team/roles, billing/plan, (v1.2) payments setup.

### 8.3 Crew PWA (mobile-only, installable, offline-tolerant)
1. **Login** — phone OTP.
2. **Today** — list of my shifts (site, time, address, map link). Big, simple, glanceable.
3. **Shift screen** — Clock In (captures GPS) → checklist (tap to complete; camera for photo items) → report issue → Clock Out. Queues locally if offline; syncs on reconnect.
4. **History** — my recent shifts (lightweight).

**Design notes:** mobile-first, large tap targets, minimal text entry for crew, works one-handed, tolerant of bad signal. Use shadcn/ui; keep it clean and fast. (Follow a real design pass; don't ship default-looking UI.)

---

## 9. Codex build sequence (phased tickets with acceptance criteria)

Hand these to Codex roughly in order. Each ticket = a PR. Keep PRs small. Every PR must pass typecheck + lint + tests + migration check.

### Phase 0 — Foundation (Week 1)
- **T0.1 Repo scaffold:** Next.js 15 + TS + Tailwind v4 + shadcn/ui; ESLint/Prettier; GitHub Actions CI (typecheck, lint, test). *AC: `pnpm dev` runs; CI green on empty PR.*
- **T0.2 DB + ORM:** Drizzle + Postgres (Supabase/Neon); implement schema §5; migrations; seed script with one demo org, owner, cleaner, client, site, checklist, a week of shifts. *AC: `drizzle migrate` creates all tables; seed populates demo data.*
- **T0.3 Auth + tenancy:** Supabase Auth (owner email magic link; cleaner phone OTP); `org_users` membership; middleware that resolves `org_id` and role; RLS policies; data-access layer always scopes by `org_id`. *AC: a user in org A cannot read org B's rows (test proves it).*
- **T0.4 App shell + nav:** authenticated layout for owner web app and separate crew PWA shell; role-based routing. *AC: owner and cleaner see different shells.*

### Phase 1 — The core loop: Schedule → Perform → Verify (Weeks 2–4)
- **T1.1 Clients & Sites CRUD** (+ checklist editor, geocode on save). *AC: create client→site→checklist; lat/lng stored.*
- **T1.2 Scheduling** (one-off + recurring via RRULE; calendar + list; assign/reassign; nightly job to materialize recurring shifts). *AC: a weekly rule generates correct upcoming shifts; reassignment works; job idempotent.*
- **T1.3 Crew PWA — Today + Shift flow** (clock in w/ GPS + geofence calc; checklist complete; photo capture→storage; report issue; clock out; **offline queue + sync**). *AC: full shift completable on a phone; works offline then syncs; geofence flag correct.*
- **T1.4 Owner verification view** (shift detail: times, geofence, checklist %, photo gallery, issues). *AC: owner sees everything a completed shift produced.*
- **T1.5 Dashboard v1** (today's coverage, missed shifts, open issues). *AC: numbers match seed/live data.*

### Phase 2 — AI layer (Weeks 4–6, can overlap Phase 1)
- **T2.1 AI service wrapper** (`/lib/ai`: client, model routing, prompt-caching, Zod JSON parsing + repair, `ai_runs` logging, cost calc). *AC: a test capability logs tokens/cost; bad-JSON path repairs once then falls back.*
- **T2.2 Quote generator** (form → Sonnet 4.6 → itemized quote; persists to `quotes`; few-shot from org's past won/lost quotes). *AC: realistic itemized quote; line items sum to total; assumptions surfaced.*
- **T2.3 Proposal writer** (quote → proposal text; editable). *AC: coherent, grounded proposal; no fabricated credentials.*
- **T2.4 Issue triage** (Haiku) + **photo quality check** (Sonnet vision, advisory flags; nightly Batch job). *AC: issues get severity/summary; photos get advisory pass/flag; never auto-penalizes.*
- **T2.5 Comms assistant** (draft replies to messages/issues; owner approves). *AC: draft uses real data; unknowns become [brackets].*

### Phase 3 — Quotes→Invoices→Send (Weeks 6–8)
- **T3.1 Quotes UI** (create/edit/send via Resend; status tracking incl. won/lost). *AC: send email with proposal + quote; status updates.*
- **T3.2 Invoicing** (generate from completed shifts/contract; line items; per-org invoice numbers; send via Resend; manual mark-paid). *AC: invoice totals correct; sequence unique; status lifecycle works.*
- **T3.3 Email templates** (React Email: login, quote, invoice, reminders). *AC: render + deliver in test.*
- **T3.4 Polish + onboarding wizard + empty states + error/loading states + Sentry + PostHog.* *AC: new user reaches first value <10 min; errors tracked.*

**→ MVP COMPLETE. Ship to design partners here.** (See §11.)

### Phase 4 — v1.1 (post-MVP, after initial usage)
- Twilio SMS notifications + crew OTP hardening; **SMS agent**; Spanish UI; lightweight **client portal** (view invoices/quotes, report issue); payroll/timesheet CSV export.

### Phase 5 — v1.2 — **Embedded payments (Act II)**
- Stripe Connect onboarding (embedded components); destination charges + application fee; webhooks; "Pay now" on invoices; payout reporting. *Only after retention + demand are proven.* This is the ARPU multiplier.

### Testing & quality (all phases)
- Unit tests for domain logic (scheduling/recurrence, invoice math, geofence, AI parsing). Integration tests for the core loop and tenancy isolation. Seed-based E2E for the crew shift flow and owner verification. Treat tenancy-isolation tests as release-blocking.

---

## 10. Repo, structure, env

### 10.1 Tooling
- **Package manager:** pnpm. **Monorepo?** Not needed in v1 — single Next.js app. (If splitting later, use Turborepo.)

### 10.2 Folder structure
```
crewline/
├─ app/                         # Next.js App Router
│  ├─ (marketing)/              # public site + pricing + blog
│  ├─ (app)/                    # authenticated owner web app
│  │  ├─ dashboard/
│  │  ├─ schedule/
│  │  ├─ clients/
│  │  ├─ sites/
│  │  ├─ shifts/[id]/
│  │  ├─ quotes/
│  │  ├─ invoices/
│  │  ├─ messages/
│  │  └─ settings/
│  ├─ (crew)/                   # crew PWA routes
│  │  ├─ today/
│  │  └─ shift/[id]/
│  ├─ api/                      # webhooks (stripe, twilio), cron triggers
│  └─ actions/                  # server actions (mutations)
├─ lib/
│  ├─ db/                       # drizzle schema, client, migrations
│  ├─ domain/                   # scheduling, invoicing, geofence, quoting logic
│  ├─ ai/                       # client, routing, prompts, capabilities, parsing
│  ├─ integrations/             # stripe/, twilio/, resend/, storage/, maps/
│  ├─ auth/                     # session, org resolution, rls helpers
│  └─ jobs/                     # inngest/trigger fns (recurring shifts, reminders, nightly AI)
├─ components/                  # shadcn/ui + app components
├─ emails/                      # react-email templates
├─ tests/                       # unit + integration + e2e
├─ public/                      # PWA manifest, icons, service worker
├─ drizzle.config.ts
├─ next.config.mjs
└─ package.json
```

### 10.3 Environment variables (`.env.example`)
```
# Core
DATABASE_URL=
NEXT_PUBLIC_APP_URL=

# Auth / Supabase (or Auth.js/Clerk)
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=

# Anthropic
ANTHROPIC_API_KEY=

# Storage (Supabase Storage or Cloudflare R2)
STORAGE_BUCKET=
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=

# Email (Resend)
RESEND_API_KEY=
EMAIL_FROM=

# Maps
GOOGLE_MAPS_API_KEY=        # or MAPBOX_TOKEN

# Jobs (Inngest / Trigger.dev)
INNGEST_EVENT_KEY=
INNGEST_SIGNING_KEY=

# --- v1.1 ---
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_VERIFY_SERVICE_SID=
TWILIO_MESSAGING_SERVICE_SID=

# --- v1.2 (payments / Act II) ---
STRIPE_SECRET_KEY=
STRIPE_PUBLISHABLE_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_CONNECT_CLIENT_ID=

# Observability
SENTRY_DSN=
NEXT_PUBLIC_POSTHOG_KEY=
NEXT_PUBLIC_POSTHOG_HOST=
```

---

## 11. Go-to-market (the part that actually determines success)

The research is blunt: **distribution, not product, is what kills solo founders** (99% cite it as their #1 problem; ~42% of startups fail from no market need). Treat GTM as co-equal with the build. Run it *in parallel* starting day 1.

### 11.1 Pricing
**Model:** subscription now; **embedded-payments take rate later** (the multiplier). Hybrid is the 2026 standard. Price against the **labor budget** (a part-time office admin / the owner's nights), not "software."

Starting tiers (validate and adjust — use the "friction test": raise price until you hear "we have to think about that"):
- **Starter — ~$99/mo:** 1 admin seat, up to ~5 sites, scheduling + crew app + verification + AI quotes (capped), email invoicing.
- **Pro — ~$249/mo:** up to ~25 sites, unlimited crew, AI comms + photo checks + issue triage, recurring billing, priority support.
- **Scale — ~$499+/mo:** 25+ sites, multiple admins, SMS agent, client portal, advanced reporting.
- **Act II (v1.2):** **payments** — small application fee per transaction processed through Crewline, on top of subscription. This is where ARPU expands 2–5×.

AI usage: bundle a monthly allowance into each tier; meter overage. Buffer token COGS ~3×; caching keeps real cost in cents per quote.

### 11.2 The first-100-customers playbook (in order)
1. **Pick the sub-niche first.** Don't sell "all cleaning." Start with **one type** where verification + recurring contracts are most painful and budgets exist — e.g., **medical-office cleaning** (compliance-sensitive, values proof-of-work) or **multi-site office janitorial**. Narrow = findable + referenceable.
2. **100 conversations in the first 90 days.** Find owners via Google Maps (search "commercial cleaning [city]"), Facebook groups, LinkedIn, and trade associations (ISSA, BSCAI). Cold email/DM/call. Yield rule of thumb: ~100 outreaches → ~5 real calls. Look for *money and urgency*, not polite interest.
3. **Pre-sell 3–5 design partners** with a written agreement (scope, weekly cadence, discounted founding price, convert-to-paid trigger). Build T1.x with them watching.
4. **Found-led onboarding.** Personally set up each early customer's first site + schedule. White-glove the first 20. Your job is their first "did the crew show up?" verified shift — the aha moment.
5. **Lock the wedge that retains:** verification ("proof the work got done") + getting paid faster. Those two create the switching cost.
6. **Then turn on a repeatable channel:** SEO is the proven low-cost engine here (Buildern got 95% of revenue from SEO inbound). Publish cleaning-business operational content ("how to price a commercial cleaning contract," "janitorial bid templates," "proof of cleaning for medical offices"). Each post is a funnel. Layer referrals (cleaning owners know other owners) and a simple affiliate/referral incentive.
7. **Measure the things that change the plan** (§12).

### 11.3 Cold outreach scripts
**Cold email (to owner):**
> Subject: quick question about [Company]'s cleaning crews
>
> Hi [Name] — I build software for commercial cleaning companies. Quick question: how do you currently confirm your crews actually showed up and finished the checklist at each site — texts, photos, calls?
>
> I'm working with a few [city] cleaning owners on a tool that gives you GPS-verified clock-ins, photo proof per site, and one-tap invoicing — so you stop chasing crews and clients stop disputing service. Founding customers get it at a steep discount while we build with their input.
>
> Worth a 15-min call this week? Happy to show you what we've got.
> — [You]

**Cold DM (shorter):**
> Hey [Name], I make software for commercial cleaning companies — GPS-verified crew check-ins + photo proof of work + fast invoicing. Looking for a couple of [city] owners to build it with at a founding-customer price. Open to a quick call?

**Voicemail:**
> Hi [Name], [You] with Crewline — we help cleaning companies verify crews showed up and did the work, and get invoices paid faster. I'd love 15 minutes to learn how you handle that today. I'll follow up by email too. Thanks!

### 11.4 Landing page copy (starter)
**Hero:**
> **Know your crews showed up. Prove the work got done. Get paid faster.**
> Crewline is the all-in-one app for commercial cleaning companies — GPS-verified clock-ins, photo proof of every job, AI-built quotes, and one-tap invoicing. Built for the field, not the back office.
> **[Start free]** · [Book a 15-min demo]

**Three value props:**
- **Verified service, automatically.** GPS clock-ins and photo proof for every site, so you (and your clients) know the job got done — no more "did they actually show up?"
- **Quote in minutes, not hours.** Describe the site; our AI writes an itemized, professional quote and proposal you can send on the spot.
- **Get paid without the chase.** Turn completed jobs into invoices in one tap; (soon) let clients pay online and money lands in your account.

**Proof/closer:** social proof from design partners once you have it; "Built with [N] cleaning companies." CTA repeated.

> Note: when you build the marketing site and app UI, do a real design pass (typography, spacing, a distinctive but clean look) — default-template UI undercuts trust with buyers who are evaluating whether to run their business on you.

---

## 12. Milestones, metrics, and decision gates

### 12.1 Milestones
- **30 days:** vertical + sub-niche locked; 3–5 design partners signed; T0 + T1.1–T1.3 underway; first GPS-verified shift completed in the app by a real crew.
- **90 days:** core loop live; first paying customers; ~$5–10K MRR target and **evidence of retention** (partners using it weekly).
- **6–12 months:** one repeatable acquisition channel working (SEO/referrals); approach **$1M ARR**; still solo or +1 contractor.
- **Year 1–2:** first 1–3 hires (support/success first); add a second module; **turn on embedded payments (Act II)**; $1M→$10M ARR.
- **Year 2–5:** become the niche's operating system; payments at scale; adjacent expansion; $10M→$100M ARR.

### 12.2 Metrics to instrument from day 1 (PostHog + DB)
- **Activation:** % of new orgs that complete a first verified shift within 7 days (target >40%).
- **Retention:** weekly active orgs; logo churn <5%/mo; aim for >100% net revenue retention as payments/seats expand.
- **The wedge working:** shifts verified/week per org; invoices sent/paid; quote win rate.
- **CAC payback:** <12 months (<3 if content-driven).
- **AI unit economics:** cost per org per month from `ai_runs` (must be a small fraction of subscription price).

### 12.3 Decision gates (when to change course)
- **If, after ~6 months of focused selling, design partners won't pay** → change the sub-niche or the problem. Pivot 1–2 times deliberately (pivoters outperform both non-pivoters and over-pivoters).
- **If NRR < 100% at scale** → fix retention *before* spending on growth.
- **If a foundation-model provider or incumbent ships your core capability** → go deeper into the data/workflow/compliance moat (proprietary verification + payment data) and down-market into the niche they won't serve.
- **Capital:** default to bootstrapping (higher survival odds, full ownership). Raise a pre-seed ($250K–$1M, SAFE, from operator-angels in field services) **only** to fund hiring ahead of a *proven, compounding* revenue engine or to win a genuinely winner-take-most race — most likely around the payments (Act II) inflection.

---

## 13. Risk register (top failure modes + mitigations)
- **Distribution failure (the #1 killer):** mitigate by picking a findable sub-niche, committing to one channel (SEO + founder sales), and building in public.
- **No real need / wrong workflow:** mitigate by pre-selling and white-glove onboarding before scaling code.
- **Adoption friction with non-technical crews:** mitigate with a dead-simple, offline-tolerant crew PWA and phone-OTP login; the crew app must be effortless or owners can't roll it out.
- **Trust damage from over-aggressive AI:** keep AI advisory (photo checks flag, don't penalize; comms/quotes are drafts). Never auto-send client messages in v1.
- **Incumbent/foundation-model encroachment:** stay narrow + boring; compound proprietary verification + payment data; own the system of record.
- **Founder burnout / key-person risk:** automate ops ruthlessly, build a contractor bench, keep scope disciplined (the "ONE workflow" rule exists for your sanity too).
- **Payments/compliance exposure (v1.2):** lean on Stripe Connect for KYC/PCI/1099s; verify webhook signatures; keep clear money-movement records.

---

## 14. The first 30 days, literally (founder checklist)
1. **Lock the sub-niche** (recommend: medical-office or multi-site office janitorial). Write the one-line customer definition on a sticky note.
2. **Start 100 conversations.** Build the list (Google Maps + associations), send the §11.3 scripts. Book calls.
3. **Sign 3–5 design partners** with a written founding-customer agreement.
4. **Kick off Codex on Phase 0 + T1.1–T1.3.** Stand up repo, schema, auth/tenancy, then Clients/Sites and the crew shift flow.
5. **Stand up the landing page** (§11.4) + a waitlist/booking link; publish the first 1–2 SEO articles.
6. **Get one real crew to complete one GPS-verified shift in the app.** That's your first proof point and your first demo.

---

*End of spec. Build the ONE loop, get a real crew clocking in and a real owner verifying work, sign paying design partners, then layer payments. That sequence — not feature breadth — is what gives this the highest probability of becoming very large.*
