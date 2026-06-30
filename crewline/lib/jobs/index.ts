import { db } from '@/lib/db';
import { orgs } from '@/lib/db/schema';
import { materializeRecurrences, markMissedShifts } from '@/lib/domain/scheduling';
import { aiEnabled } from '@/lib/ai/enabled';
import { checkPhoto } from '@/lib/ai/capabilities';
import { getObject } from '@/lib/integrations/storage';
import {
  listUnreviewedPhotos,
  photoTaskLabel,
  rollupShiftQuality,
  setPhotoReview,
} from '@/lib/domain/photos';

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

function mediaTypeFromKey(key: string): 'image/jpeg' | 'image/png' | 'image/webp' {
  const ext = key.split('.').pop()?.toLowerCase();
  if (ext === 'png') return 'image/png';
  if (ext === 'webp') return 'image/webp';
  return 'image/jpeg';
}

/**
 * Nightly advisory proof-photo quality scan (spec T2.4). Reviews each
 * unreviewed photo with the vision capability and rolls up a per-shift quality
 * score. Advisory only — never penalizes a crew. Skipped if no API key.
 * Returns the number of photos reviewed.
 */
export async function runPhotoQualityScan(): Promise<number> {
  if (!aiEnabled()) return 0;
  let reviewed = 0;
  for (const orgId of await allOrgIds()) {
    const photos = await listUnreviewedPhotos(orgId, 100);
    const touchedShifts = new Set<string>();
    for (const { photo } of photos) {
      const bytes = await getObject(photo.storageKey);
      if (!bytes) continue; // image not on disk (e.g. demo seed) — skip
      try {
        const label = await photoTaskLabel(orgId, photo.id);
        const out = await checkPhoto(orgId, {
          imageBase64: bytes.toString('base64'),
          mediaType: mediaTypeFromKey(photo.storageKey),
          taskLabel: label,
        });
        await setPhotoReview(orgId, photo.id, { pass: out.pass, reason: out.reason });
        touchedShifts.add(photo.shiftId);
        reviewed += 1;
      } catch (err) {
        console.error('photo scan skipped one:', err);
      }
    }
    await rollupShiftQuality(orgId, [...touchedShifts]);
  }
  return reviewed;
}
