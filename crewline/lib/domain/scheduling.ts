import { RRule } from 'rrule';
import { DateTime } from 'luxon';
import { and, asc, eq, gte, lte, lt } from 'drizzle-orm';
import { db } from '@/lib/db';
import { recurrences, shifts, sites, orgs, users } from '@/lib/db/schema';
import { notFound } from './errors';
import { recurrenceInput, shiftInput, type RecurrenceInput, type ShiftInput } from './schemas';

/**
 * Convert local wall-clock components in a timezone to a real UTC instant.
 */
function localToInstant(
  y: number,
  mo: number,
  d: number,
  h: number,
  mi: number,
  zone: string,
): Date {
  const dt = DateTime.fromObject(
    { year: y, month: mo, day: d, hour: h, minute: mi },
    { zone },
  );
  return dt.toJSDate();
}

/**
 * Enumerate the scheduled_start instants for a recurrence rule between [from, to].
 *
 * rrule is timezone-naive, so we use the documented UTC-floating workaround:
 * feed dtstart/window as UTC dates carrying the *local* wall-clock components,
 * enumerate, then re-interpret each occurrence's UTC components as local
 * wall-clock in the org timezone and convert to a true instant (DST-correct).
 */
export function occurrencesBetween(
  rruleStr: string,
  startTimeLocal: string,
  zone: string,
  from: Date,
  to: Date,
): Date[] {
  const [hh, mm] = startTimeLocal.split(':').map((s) => parseInt(s, 10));
  const hour = Number.isFinite(hh) ? hh : 18;
  const minute = Number.isFinite(mm) ? mm : 0;

  // Anchor dtstart at the local "from" date, at the rule's local start time.
  const fromLocal = DateTime.fromJSDate(from, { zone });
  const toLocal = DateTime.fromJSDate(to, { zone });

  const dtstart = new Date(
    Date.UTC(fromLocal.year, fromLocal.month - 1, fromLocal.day, hour, minute),
  );

  const options = RRule.parseString(rruleStr);
  options.dtstart = dtstart;
  const rule = new RRule(options);

  // Window bounds as UTC-floating local components.
  const after = new Date(Date.UTC(fromLocal.year, fromLocal.month - 1, fromLocal.day, 0, 0));
  const before = new Date(
    Date.UTC(toLocal.year, toLocal.month - 1, toLocal.day, 23, 59, 59),
  );

  const occ = rule.between(after, before, true);
  return occ.map((o) =>
    localToInstant(
      o.getUTCFullYear(),
      o.getUTCMonth() + 1,
      o.getUTCDate(),
      hour,
      minute,
      zone,
    ),
  );
}

async function getOrgTimezone(orgId: string): Promise<string> {
  const rows = await db.select({ tz: orgs.timezone }).from(orgs).where(eq(orgs.id, orgId)).limit(1);
  return rows[0]?.tz ?? 'America/Chicago';
}

async function assertSiteInOrg(orgId: string, siteId: string) {
  const rows = await db
    .select({ id: sites.id })
    .from(sites)
    .where(and(eq(sites.orgId, orgId), eq(sites.id, siteId)))
    .limit(1);
  if (!rows[0]) notFound('Site');
}

// --- one-off shifts ----------------------------------------------------------
export async function createShift(orgId: string, input: ShiftInput) {
  const data = shiftInput.parse(input);
  await assertSiteInOrg(orgId, data.siteId);
  const rows = await db
    .insert(shifts)
    .values({
      orgId,
      siteId: data.siteId,
      assignedUserId: data.assignedUserId ?? null,
      scheduledStart: data.scheduledStart,
      scheduledEnd: data.scheduledEnd,
      status: 'scheduled',
    })
    .returning();
  return rows[0];
}

export async function assignShift(orgId: string, shiftId: string, userId: string | null) {
  const rows = await db
    .update(shifts)
    .set({ assignedUserId: userId })
    .where(and(eq(shifts.orgId, orgId), eq(shifts.id, shiftId)))
    .returning();
  if (!rows[0]) notFound('Shift');
  return rows[0];
}

export async function cancelShift(orgId: string, shiftId: string) {
  const rows = await db
    .update(shifts)
    .set({ status: 'canceled' })
    .where(and(eq(shifts.orgId, orgId), eq(shifts.id, shiftId)))
    .returning();
  if (!rows[0]) notFound('Shift');
  return rows[0];
}

export async function listShiftsInRange(orgId: string, from: Date, to: Date) {
  return db
    .select({
      shift: shifts,
      siteName: sites.name,
      assigneeName: users.fullName,
    })
    .from(shifts)
    .innerJoin(sites, eq(sites.id, shifts.siteId))
    .leftJoin(users, eq(users.id, shifts.assignedUserId))
    .where(
      and(
        eq(shifts.orgId, orgId),
        gte(shifts.scheduledStart, from),
        lte(shifts.scheduledStart, to),
      ),
    )
    .orderBy(asc(shifts.scheduledStart));
}

// --- recurrences -------------------------------------------------------------
export async function createRecurrence(orgId: string, input: RecurrenceInput) {
  const data = recurrenceInput.parse(input);
  await assertSiteInOrg(orgId, data.siteId);
  // Validate the RRULE string up front so a bad rule never reaches the job.
  RRule.parseString(data.rrule);
  const rows = await db
    .insert(recurrences)
    .values({
      orgId,
      siteId: data.siteId,
      rrule: data.rrule,
      defaultAssignedUserId: data.defaultAssignedUserId ?? null,
      startTimeLocal: data.startTimeLocal,
      durationMinutes: data.durationMinutes,
      active: data.active,
    })
    .returning();
  return rows[0];
}

export async function listRecurrences(orgId: string) {
  return db.select().from(recurrences).where(eq(recurrences.orgId, orgId));
}

/**
 * Materialize concrete shifts from active recurrence rules over a horizon.
 * Idempotent: the unique index (recurrence_id, scheduled_start) means re-running
 * inserts nothing new. Returns the number of shifts created.
 */
export async function materializeRecurrences(
  orgId: string,
  horizonDays = 14,
  fromDate?: Date,
): Promise<number> {
  const zone = await getOrgTimezone(orgId);
  const from = fromDate ?? new Date();
  const to = new Date(from.getTime() + horizonDays * 24 * 60 * 60 * 1000);

  const rules = await db
    .select()
    .from(recurrences)
    .where(and(eq(recurrences.orgId, orgId), eq(recurrences.active, true)));

  let created = 0;
  for (const rule of rules) {
    const starts = occurrencesBetween(rule.rrule, rule.startTimeLocal, zone, from, to);
    if (!starts.length) continue;

    const values = starts.map((start) => ({
      orgId,
      siteId: rule.siteId,
      assignedUserId: rule.defaultAssignedUserId ?? null,
      scheduledStart: start,
      scheduledEnd: new Date(start.getTime() + rule.durationMinutes * 60 * 1000),
      status: 'scheduled' as const,
      recurrenceId: rule.id,
    }));

    // onConflictDoNothing on the (recurrence_id, scheduled_start) unique index.
    const inserted = await db
      .insert(shifts)
      .values(values)
      .onConflictDoNothing({ target: [shifts.recurrenceId, shifts.scheduledStart] })
      .returning({ id: shifts.id });
    created += inserted.length;
  }
  return created;
}

/**
 * Mark shifts that are past their scheduled end and were never started as
 * 'missed' (nightly job). Returns affected shift ids.
 */
export async function markMissedShifts(orgId: string, asOf?: Date): Promise<string[]> {
  const now = asOf ?? new Date();
  const rows = await db
    .update(shifts)
    .set({ status: 'missed' })
    .where(
      and(
        eq(shifts.orgId, orgId),
        eq(shifts.status, 'scheduled'),
        lt(shifts.scheduledEnd, now),
      ),
    )
    .returning({ id: shifts.id });
  return rows.map((r) => r.id);
}

export async function listAssignableCrew(orgId: string) {
  // org members with role cleaner/admin/owner can be assigned shifts.
  const { orgUsers } = await import('@/lib/db/schema');
  return db
    .select({ id: users.id, fullName: users.fullName, phone: users.phone, role: orgUsers.role })
    .from(orgUsers)
    .innerJoin(users, eq(users.id, orgUsers.userId))
    .where(and(eq(orgUsers.orgId, orgId), eq(orgUsers.status, 'active')));
}
