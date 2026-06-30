import { db } from '@/lib/db';
import { orgs } from '@/lib/db/schema';
import { materializeRecurrences, markMissedShifts } from '@/lib/domain/scheduling';

/**
 * Background jobs (spec §4). In the MVP these are plain domain functions invoked
 * by cron-guarded API routes (Vercel Cron / any scheduler hitting /api/cron/*).
 * They are pure and unit-testable; swapping to Inngest/Trigger.dev later only
 * changes the transport, not this logic.
 */
async function allOrgIds(): Promise<string[]> {
  const rows = await db.select({ id: orgs.id }).from(orgs);
  return rows.map((r) => r.id);
}

export async function runMaterializeAllOrgs(horizonDays = 14): Promise<number> {
  let total = 0;
  for (const orgId of await allOrgIds()) {
    total += await materializeRecurrences(orgId, horizonDays);
  }
  return total;
}

export async function runMarkMissedAllOrgs(): Promise<number> {
  let total = 0;
  for (const orgId of await allOrgIds()) {
    const ids = await markMissedShifts(orgId);
    total += ids.length;
  }
  return total;
}
