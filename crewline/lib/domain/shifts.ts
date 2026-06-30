import { and, asc, desc, eq, gte, lte } from 'drizzle-orm';
import { DateTime } from 'luxon';
import { db } from '@/lib/db';
import {
  issues,
  orgs,
  shiftChecklistResults,
  shiftPhotos,
  shifts,
  siteChecklistItems,
  sites,
  users,
} from '@/lib/db/schema';
import { checkGeofence } from './geofence';
import { DomainError, forbidden, notFound } from './errors';

async function getOrgTimezone(orgId: string): Promise<string> {
  const rows = await db.select({ tz: orgs.timezone }).from(orgs).where(eq(orgs.id, orgId)).limit(1);
  return rows[0]?.tz ?? 'America/Chicago';
}

/** Today's shifts (org tz) assigned to a crew member. */
export async function getCrewToday(orgId: string, userId: string, asOf?: Date) {
  const zone = await getOrgTimezone(orgId);
  const ref = DateTime.fromJSDate(asOf ?? new Date(), { zone });
  const dayStart = ref.startOf('day').toJSDate();
  const dayEnd = ref.endOf('day').toJSDate();

  return db
    .select({ shift: shifts, siteName: sites.name, address: sites.address })
    .from(shifts)
    .innerJoin(sites, eq(sites.id, shifts.siteId))
    .where(
      and(
        eq(shifts.orgId, orgId),
        eq(shifts.assignedUserId, userId),
        gte(shifts.scheduledStart, dayStart),
        lte(shifts.scheduledStart, dayEnd),
      ),
    )
    .orderBy(asc(shifts.scheduledStart));
}

export async function getCrewHistory(orgId: string, userId: string, limit = 20) {
  return db
    .select({ shift: shifts, siteName: sites.name })
    .from(shifts)
    .innerJoin(sites, eq(sites.id, shifts.siteId))
    .where(and(eq(shifts.orgId, orgId), eq(shifts.assignedUserId, userId)))
    .orderBy(desc(shifts.scheduledStart))
    .limit(limit);
}

/** Full shift detail (owner verification view + crew shift screen). */
export async function getShiftDetail(orgId: string, shiftId: string) {
  const rows = await db
    .select({
      shift: shifts,
      site: sites,
      assigneeName: users.fullName,
    })
    .from(shifts)
    .innerJoin(sites, eq(sites.id, shifts.siteId))
    .leftJoin(users, eq(users.id, shifts.assignedUserId))
    .where(and(eq(shifts.orgId, orgId), eq(shifts.id, shiftId)))
    .limit(1);
  const base = rows[0];
  if (!base) return null;

  const [results, photos, shiftIssues] = await Promise.all([
    db
      .select()
      .from(shiftChecklistResults)
      .where(and(eq(shiftChecklistResults.orgId, orgId), eq(shiftChecklistResults.shiftId, shiftId))),
    db
      .select()
      .from(shiftPhotos)
      .where(and(eq(shiftPhotos.orgId, orgId), eq(shiftPhotos.shiftId, shiftId)))
      .orderBy(asc(shiftPhotos.createdAt)),
    db
      .select()
      .from(issues)
      .where(and(eq(issues.orgId, orgId), eq(issues.shiftId, shiftId))),
  ]);

  const total = results.length;
  const done = results.filter((r) => r.completed).length;
  const checklistPct = total === 0 ? 0 : Math.round((done / total) * 100);

  return { ...base, results, photos, issues: shiftIssues, checklistPct, checklistDone: done, checklistTotal: total };
}

/** Snapshot the site's checklist into per-shift results (once). */
async function ensureChecklistSnapshot(orgId: string, shiftId: string, siteId: string) {
  const existing = await db
    .select({ id: shiftChecklistResults.id })
    .from(shiftChecklistResults)
    .where(and(eq(shiftChecklistResults.orgId, orgId), eq(shiftChecklistResults.shiftId, shiftId)))
    .limit(1);
  if (existing[0]) return;

  const items = await db
    .select()
    .from(siteChecklistItems)
    .where(and(eq(siteChecklistItems.orgId, orgId), eq(siteChecklistItems.siteId, siteId)))
    .orderBy(asc(siteChecklistItems.sortOrder));

  if (!items.length) return;
  await db.insert(shiftChecklistResults).values(
    items.map((it) => ({
      orgId,
      shiftId,
      checklistItemId: it.id,
      labelSnapshot: it.label,
      completed: false,
    })),
  );
}

async function loadOwnedShift(orgId: string, shiftId: string, userId?: string) {
  const rows = await db
    .select({ shift: shifts, site: sites })
    .from(shifts)
    .innerJoin(sites, eq(sites.id, shifts.siteId))
    .where(and(eq(shifts.orgId, orgId), eq(shifts.id, shiftId)))
    .limit(1);
  const row = rows[0];
  if (!row) notFound('Shift');
  if (userId && row.shift.assignedUserId !== userId) {
    forbidden('You are not assigned to this shift.');
  }
  return row;
}

export type ClockInResult = { withinGeofence: boolean; distanceM: number };

export async function clockIn(
  orgId: string,
  shiftId: string,
  userId: string,
  lat: number,
  lng: number,
  at?: Date,
): Promise<ClockInResult> {
  const { shift, site } = await loadOwnedShift(orgId, shiftId, userId);
  if (shift.status === 'completed' || shift.status === 'canceled') {
    throw new DomainError(`Cannot clock in to a ${shift.status} shift`, 'conflict');
  }
  const geo = checkGeofence(site, lat, lng);
  const when = at ?? new Date();

  await db
    .update(shifts)
    .set({
      status: 'in_progress',
      clockInAt: when,
      clockInLat: lat,
      clockInLng: lng,
      clockInWithinGeofence: geo.withinGeofence,
    })
    .where(and(eq(shifts.orgId, orgId), eq(shifts.id, shiftId)));

  await ensureChecklistSnapshot(orgId, shiftId, site.id);
  return { withinGeofence: geo.withinGeofence, distanceM: geo.distanceM };
}

export async function setChecklistResult(
  orgId: string,
  shiftId: string,
  userId: string,
  resultId: string,
  completed: boolean,
  at?: Date,
) {
  await loadOwnedShift(orgId, shiftId, userId);
  const rows = await db
    .update(shiftChecklistResults)
    .set({ completed, completedAt: completed ? (at ?? new Date()) : null })
    .where(
      and(
        eq(shiftChecklistResults.orgId, orgId),
        eq(shiftChecklistResults.shiftId, shiftId),
        eq(shiftChecklistResults.id, resultId),
      ),
    )
    .returning();
  if (!rows[0]) notFound('Checklist item');
  return rows[0];
}

export async function addShiftPhoto(
  orgId: string,
  shiftId: string,
  userId: string,
  photo: { storageKey: string; url: string; caption?: string | null; resultId?: string | null },
) {
  await loadOwnedShift(orgId, shiftId, userId);
  const rows = await db
    .insert(shiftPhotos)
    .values({
      orgId,
      shiftId,
      storageKey: photo.storageKey,
      url: photo.url,
      caption: photo.caption ?? null,
    })
    .returning();
  const created = rows[0];
  if (photo.resultId) {
    await db
      .update(shiftChecklistResults)
      .set({ photoId: created.id })
      .where(
        and(
          eq(shiftChecklistResults.orgId, orgId),
          eq(shiftChecklistResults.shiftId, shiftId),
          eq(shiftChecklistResults.id, photo.resultId),
        ),
      );
  }
  return created;
}

export async function clockOut(orgId: string, shiftId: string, userId: string, at?: Date) {
  const { shift } = await loadOwnedShift(orgId, shiftId, userId);
  if (shift.status !== 'in_progress') {
    throw new DomainError('Shift is not in progress', 'conflict');
  }
  const rows = await db
    .update(shifts)
    .set({ status: 'completed', clockOutAt: at ?? new Date() })
    .where(and(eq(shifts.orgId, orgId), eq(shifts.id, shiftId)))
    .returning();
  return rows[0];
}
