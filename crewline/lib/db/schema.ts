/**
 * Crewline data model — spec §5.
 *
 * Conventions:
 * - UUID primary keys via gen_random_uuid().
 * - All timestamps are timestamptz.
 * - Every domain table (all except `users`) carries org_id for tenancy.
 *   (`orgs` carries its own id; `users` mirror Supabase/auth identities and are
 *   linked to orgs through org_users — see spec §5.2.)
 */
import {
  pgTable,
  uuid,
  text,
  integer,
  boolean,
  doublePrecision,
  numeric,
  timestamp,
  date,
  jsonb,
  index,
  uniqueIndex,
} from 'drizzle-orm/pg-core';

const now = () => timestamp('created_at', { withTimezone: true }).notNull().defaultNow();

// --- orgs: the tenant (a cleaning company) ------------------------------------
export const orgs = pgTable('orgs', {
  id: uuid('id').primaryKey().defaultRandom(),
  name: text('name').notNull(),
  timezone: text('timezone').notNull().default('America/Chicago'),
  plan: text('plan').notNull().default('starter'), // 'starter' | 'pro' | 'scale'
  createdAt: now(),
});

// --- users: a person (owner or cleaner) --------------------------------------
export const users = pgTable('users', {
  id: uuid('id').primaryKey().defaultRandom(), // mirrors auth user id
  email: text('email'), // nullable for crew who only use phone
  phone: text('phone'), // E.164; used for crew OTP + SMS
  fullName: text('full_name'),
  // Auth material for the MVP self-contained session layer (see README).
  // Owners/admins authenticate with a password hash; crew with phone OTP.
  passwordHash: text('password_hash'),
  createdAt: now(),
});

// --- org_users: membership + role (many-to-many) -----------------------------
export const orgUsers = pgTable(
  'org_users',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    userId: uuid('user_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    role: text('role').notNull(), // 'owner' | 'admin' | 'cleaner'
    status: text('status').notNull().default('active'), // 'active' | 'invited' | 'disabled'
    hourlyRateCents: integer('hourly_rate_cents'),
    createdAt: now(),
  },
  (t) => [uniqueIndex('org_users_org_user_uq').on(t.orgId, t.userId)],
);

// --- clients: the cleaning company's customers -------------------------------
export const clients = pgTable(
  'clients',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    name: text('name').notNull(),
    contactName: text('contact_name'),
    contactEmail: text('contact_email'),
    contactPhone: text('contact_phone'),
    billingTerms: text('billing_terms').notNull().default('net30'), // net15|net30|due_on_receipt
    notes: text('notes'),
    createdAt: now(),
  },
  (t) => [index('clients_org_idx').on(t.orgId)],
);

// --- sites: a physical location under a client -------------------------------
export const sites = pgTable(
  'sites',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    clientId: uuid('client_id')
      .notNull()
      .references(() => clients.id, { onDelete: 'restrict' }),
    name: text('name').notNull(),
    address: text('address'),
    lat: doublePrecision('lat'),
    lng: doublePrecision('lng'),
    geofenceRadiusM: integer('geofence_radius_m').notNull().default(150),
    siteType: text('site_type').notNull().default('office'), // office|medical|retail|school|industrial|other
    squareFootage: integer('square_footage'),
    serviceFrequency: text('service_frequency').notNull().default('weekly'), // daily|weekly|biweekly|monthly|custom
    contractRateCents: integer('contract_rate_cents'),
    active: boolean('active').notNull().default(true),
    createdAt: now(),
  },
  (t) => [index('sites_org_client_idx').on(t.orgId, t.clientId)],
);

// --- site_checklist_items: template tasks for a site -------------------------
export const siteChecklistItems = pgTable(
  'site_checklist_items',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    siteId: uuid('site_id')
      .notNull()
      .references(() => sites.id, { onDelete: 'cascade' }),
    label: text('label').notNull(),
    area: text('area'),
    requiresPhoto: boolean('requires_photo').notNull().default(false),
    sortOrder: integer('sort_order').notNull().default(0),
  },
  (t) => [index('checklist_items_site_idx').on(t.siteId)],
);

// --- recurrences: rule that generates shifts ---------------------------------
export const recurrences = pgTable(
  'recurrences',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    siteId: uuid('site_id')
      .notNull()
      .references(() => sites.id, { onDelete: 'cascade' }),
    rrule: text('rrule').notNull(), // iCal RRULE string
    defaultAssignedUserId: uuid('default_assigned_user_id').references(() => users.id, {
      onDelete: 'set null',
    }),
    startTimeLocal: text('start_time_local').notNull().default('18:00'), // "18:00"
    durationMinutes: integer('duration_minutes').notNull().default(120),
    active: boolean('active').notNull().default(true),
    createdAt: now(),
  },
  (t) => [index('recurrences_org_site_idx').on(t.orgId, t.siteId)],
);

// --- shifts: a scheduled instance of cleaning a site -------------------------
export const shifts = pgTable(
  'shifts',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    siteId: uuid('site_id')
      .notNull()
      .references(() => sites.id, { onDelete: 'cascade' }),
    assignedUserId: uuid('assigned_user_id').references(() => users.id, { onDelete: 'set null' }),
    scheduledStart: timestamp('scheduled_start', { withTimezone: true }).notNull(),
    scheduledEnd: timestamp('scheduled_end', { withTimezone: true }).notNull(),
    status: text('status').notNull().default('scheduled'), // scheduled|in_progress|completed|missed|canceled
    clockInAt: timestamp('clock_in_at', { withTimezone: true }),
    clockInLat: doublePrecision('clock_in_lat'),
    clockInLng: doublePrecision('clock_in_lng'),
    clockInWithinGeofence: boolean('clock_in_within_geofence'),
    clockOutAt: timestamp('clock_out_at', { withTimezone: true }),
    recurrenceId: uuid('recurrence_id').references(() => recurrences.id, { onDelete: 'set null' }),
    aiQualityScore: integer('ai_quality_score'), // 0-100, nightly AI scan
    aiQualityNotes: text('ai_quality_notes'),
    createdAt: now(),
  },
  (t) => [
    index('shifts_org_start_idx').on(t.orgId, t.scheduledStart),
    index('shifts_org_status_idx').on(t.orgId, t.status),
    index('shifts_assignee_start_idx').on(t.assignedUserId, t.scheduledStart),
    // Idempotency for recurring materialization: one shift per (recurrence, start).
    uniqueIndex('shifts_recurrence_start_uq').on(t.recurrenceId, t.scheduledStart),
  ],
);

// --- shift_checklist_results: what got done on a specific shift ---------------
export const shiftChecklistResults = pgTable(
  'shift_checklist_results',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    shiftId: uuid('shift_id')
      .notNull()
      .references(() => shifts.id, { onDelete: 'cascade' }),
    checklistItemId: uuid('checklist_item_id').references(() => siteChecklistItems.id, {
      onDelete: 'set null',
    }),
    labelSnapshot: text('label_snapshot').notNull(),
    completed: boolean('completed').notNull().default(false),
    completedAt: timestamp('completed_at', { withTimezone: true }),
    photoId: uuid('photo_id'), // FK added after shift_photos (circular); see relations note
  },
  (t) => [index('checklist_results_shift_idx').on(t.shiftId)],
);

// --- shift_photos: proof photos ----------------------------------------------
export const shiftPhotos = pgTable(
  'shift_photos',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    shiftId: uuid('shift_id')
      .notNull()
      .references(() => shifts.id, { onDelete: 'cascade' }),
    storageKey: text('storage_key').notNull(),
    url: text('url').notNull(),
    caption: text('caption'),
    aiPass: boolean('ai_pass'), // advisory vision check result
    aiReason: text('ai_reason'),
    createdAt: now(),
  },
  (t) => [index('shift_photos_shift_idx').on(t.shiftId)],
);

// --- issues: problems reported by crew or client -----------------------------
export const issues = pgTable(
  'issues',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    siteId: uuid('site_id').references(() => sites.id, { onDelete: 'set null' }),
    shiftId: uuid('shift_id').references(() => shifts.id, { onDelete: 'set null' }),
    reportedByUserId: uuid('reported_by_user_id').references(() => users.id, {
      onDelete: 'set null',
    }),
    source: text('source').notNull().default('crew'), // crew|client|ai
    severity: text('severity').notNull().default('medium'), // low|medium|high
    status: text('status').notNull().default('open'), // open|acknowledged|resolved
    description: text('description').notNull(),
    aiSummary: text('ai_summary'),
    createdAt: now(),
  },
  (t) => [index('issues_org_status_idx').on(t.orgId, t.status)],
);

// --- quotes: AI-generated proposals ------------------------------------------
export const quotes = pgTable(
  'quotes',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    clientId: uuid('client_id').references(() => clients.id, { onDelete: 'set null' }),
    siteId: uuid('site_id').references(() => sites.id, { onDelete: 'set null' }),
    prospectName: text('prospect_name'),
    inputsJson: jsonb('inputs_json'),
    lineItemsJson: jsonb('line_items_json'),
    totalCents: integer('total_cents').notNull().default(0),
    frequency: text('frequency').notNull().default('monthly'),
    status: text('status').notNull().default('draft'), // draft|sent|won|lost
    proposalText: text('proposal_text'),
    createdAt: now(),
  },
  (t) => [index('quotes_org_status_idx').on(t.orgId, t.status)],
);

// --- invoices ----------------------------------------------------------------
export const invoices = pgTable(
  'invoices',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    clientId: uuid('client_id')
      .notNull()
      .references(() => clients.id, { onDelete: 'restrict' }),
    siteId: uuid('site_id').references(() => sites.id, { onDelete: 'set null' }),
    number: text('number').notNull(), // human-friendly, per-org sequence
    periodStart: date('period_start'),
    periodEnd: date('period_end'),
    subtotalCents: integer('subtotal_cents').notNull().default(0),
    taxCents: integer('tax_cents').notNull().default(0),
    totalCents: integer('total_cents').notNull().default(0),
    status: text('status').notNull().default('draft'), // draft|sent|paid|overdue|void
    dueDate: date('due_date'),
    sentAt: timestamp('sent_at', { withTimezone: true }),
    paidAt: timestamp('paid_at', { withTimezone: true }),
    stripePaymentIntentId: text('stripe_payment_intent_id'), // v1.2
    createdAt: now(),
  },
  (t) => [
    index('invoices_org_status_due_idx').on(t.orgId, t.status, t.dueDate),
    uniqueIndex('invoices_org_number_uq').on(t.orgId, t.number),
  ],
);

// --- invoice_line_items ------------------------------------------------------
export const invoiceLineItems = pgTable(
  'invoice_line_items',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    invoiceId: uuid('invoice_id')
      .notNull()
      .references(() => invoices.id, { onDelete: 'cascade' }),
    description: text('description').notNull(),
    quantity: numeric('quantity').notNull().default('1'),
    unitPriceCents: integer('unit_price_cents').notNull().default(0),
    amountCents: integer('amount_cents').notNull().default(0),
  },
  (t) => [index('invoice_line_items_invoice_idx').on(t.invoiceId)],
);

// --- messages: client communication log (and AI drafts) ----------------------
export const messages = pgTable(
  'messages',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    clientId: uuid('client_id').references(() => clients.id, { onDelete: 'set null' }),
    siteId: uuid('site_id').references(() => sites.id, { onDelete: 'set null' }),
    direction: text('direction').notNull(), // inbound|outbound
    channel: text('channel').notNull().default('internal'), // sms|email|internal
    body: text('body').notNull(),
    aiGenerated: boolean('ai_generated').notNull().default(false),
    status: text('status').notNull().default('draft'), // draft|sent|received
    createdAt: now(),
  },
  (t) => [index('messages_org_client_idx').on(t.orgId, t.clientId)],
);

// --- ai_runs: audit log of every AI call -------------------------------------
export const aiRuns = pgTable(
  'ai_runs',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    orgId: uuid('org_id')
      .notNull()
      .references(() => orgs.id, { onDelete: 'cascade' }),
    capability: text('capability').notNull(), // quote|proposal|comms_draft|photo_check|issue_triage|sms_agent
    model: text('model').notNull(),
    inputTokens: integer('input_tokens').notNull().default(0),
    outputTokens: integer('output_tokens').notNull().default(0),
    cachedInputTokens: integer('cached_input_tokens').notNull().default(0),
    costCents: numeric('cost_cents').notNull().default('0'),
    latencyMs: integer('latency_ms').notNull().default(0),
    inputRef: jsonb('input_ref'),
    outputRef: jsonb('output_ref'),
    ok: boolean('ok').notNull().default(true),
    createdAt: now(),
  },
  (t) => [index('ai_runs_org_capability_idx').on(t.orgId, t.capability)],
);

// --- stripe_accounts (v1.2 — scaffold now, populate later) -------------------
export const stripeAccounts = pgTable('stripe_accounts', {
  id: uuid('id').primaryKey().defaultRandom(),
  orgId: uuid('org_id')
    .notNull()
    .references(() => orgs.id, { onDelete: 'cascade' }),
  stripeConnectedAccountId: text('stripe_connected_account_id'),
  chargesEnabled: boolean('charges_enabled').notNull().default(false),
  payoutsEnabled: boolean('payouts_enabled').notNull().default(false),
  onboardingStatus: text('onboarding_status'),
  createdAt: now(),
});

// Convenience type exports
export type Org = typeof orgs.$inferSelect;
export type User = typeof users.$inferSelect;
export type OrgUser = typeof orgUsers.$inferSelect;
export type Client = typeof clients.$inferSelect;
export type Site = typeof sites.$inferSelect;
export type SiteChecklistItem = typeof siteChecklistItems.$inferSelect;
export type Recurrence = typeof recurrences.$inferSelect;
export type Shift = typeof shifts.$inferSelect;
export type ShiftChecklistResult = typeof shiftChecklistResults.$inferSelect;
export type ShiftPhoto = typeof shiftPhotos.$inferSelect;
export type Issue = typeof issues.$inferSelect;
export type Quote = typeof quotes.$inferSelect;
export type Invoice = typeof invoices.$inferSelect;
export type InvoiceLineItem = typeof invoiceLineItems.$inferSelect;
export type Message = typeof messages.$inferSelect;
export type AiRun = typeof aiRuns.$inferSelect;
export type StripeAccount = typeof stripeAccounts.$inferSelect;
