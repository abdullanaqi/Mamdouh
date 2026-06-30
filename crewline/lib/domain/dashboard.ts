import { and, eq, gte, lte, inArray } from 'drizzle-orm';
import { DateTime } from 'luxon';
import { db } from '@/lib/db';
import { invoices, issues, orgs, shiftPhotos, shifts, sites, users } from '@/lib/db/schema';

async function getOrgTimezone(orgId: string): Promise<string> {
  const rows = await db.select({ tz: orgs.timezone }).from(orgs).where(eq(orgs.id, orgId)).limit(1);
  return rows[0]?.tz ?? 'America/Chicago';
}

export type DashboardData = {
  today: {
    scheduled: number;
    inProgress: number;
    completed: number;
    missed: number;
    total: number;
  };
  outstandingInvoiceCents: number;
  outstandingInvoiceCount: number;
  openIssues: number;
  qualityFlags: number;
  todayShifts: Array<{
    id: string;
    siteName: string;
    assigneeName: string | null;
    scheduledStart: Date;
    status: string;
    withinGeofence: boolean | null;
  }>;
};

export async function getDashboard(orgId: string, asOf?: Date): Promise<DashboardData> {
  const zone = await getOrgTimezone(orgId);
  const ref = DateTime.fromJSDate(asOf ?? new Date(), { zone });
  const dayStart = ref.startOf('day').toJSDate();
  const dayEnd = ref.endOf('day').toJSDate();

  const todayShiftRows = await db
    .select({
      id: shifts.id,
      siteName: sites.name,
      assigneeName: users.fullName,
      scheduledStart: shifts.scheduledStart,
      status: shifts.status,
      withinGeofence: shifts.clockInWithinGeofence,
    })
    .from(shifts)
    .innerJoin(sites, eq(sites.id, shifts.siteId))
    .leftJoin(users, eq(users.id, shifts.assignedUserId))
    .where(
      and(
        eq(shifts.orgId, orgId),
        gte(shifts.scheduledStart, dayStart),
        lte(shifts.scheduledStart, dayEnd),
      ),
    );

  const counts = { scheduled: 0, inProgress: 0, completed: 0, missed: 0, total: todayShiftRows.length };
  for (const s of todayShiftRows) {
    if (s.status === 'scheduled') counts.scheduled += 1;
    else if (s.status === 'in_progress') counts.inProgress += 1;
    else if (s.status === 'completed') counts.completed += 1;
    else if (s.status === 'missed') counts.missed += 1;
  }

  // Outstanding invoices (sent or overdue).
  const outstanding = await db
    .select({ total: invoices.totalCents, id: invoices.id })
    .from(invoices)
    .where(and(eq(invoices.orgId, orgId), inArray(invoices.status, ['sent', 'overdue'])));
  const outstandingInvoiceCents = outstanding.reduce((sum, i) => sum + i.total, 0);

  // Open issues.
  const openIssueRows = await db
    .select({ id: issues.id })
    .from(issues)
    .where(and(eq(issues.orgId, orgId), eq(issues.status, 'open')));

  // Quality flags: proof photos where AI advised a fail.
  const flagged = await db
    .select({ id: shiftPhotos.id })
    .from(shiftPhotos)
    .where(and(eq(shiftPhotos.orgId, orgId), eq(shiftPhotos.aiPass, false)));

  return {
    today: counts,
    outstandingInvoiceCents,
    outstandingInvoiceCount: outstanding.length,
    openIssues: openIssueRows.length,
    qualityFlags: flagged.length,
    todayShifts: todayShiftRows.sort(
      (a, b) => a.scheduledStart.getTime() - b.scheduledStart.getTime(),
    ),
  };
}
