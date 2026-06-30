import { and, eq, isNull } from 'drizzle-orm';
import { db } from '@/lib/db';
import { shiftChecklistResults, shiftPhotos, shifts } from '@/lib/db/schema';

/** Proof photos not yet reviewed by the advisory AI scan, for an org. */
export async function listUnreviewedPhotos(orgId: string, limit = 100) {
  return db
    .select({ photo: shiftPhotos })
    .from(shiftPhotos)
    .where(and(eq(shiftPhotos.orgId, orgId), isNull(shiftPhotos.aiPass)))
    .limit(limit);
}

export async function setPhotoReview(
  orgId: string,
  photoId: string,
  review: { pass: boolean; reason: string },
) {
  await db
    .update(shiftPhotos)
    .set({ aiPass: review.pass, aiReason: review.reason })
    .where(and(eq(shiftPhotos.orgId, orgId), eq(shiftPhotos.id, photoId)));
}

/** The checklist label a photo is attached to (best label for the vision check). */
export async function photoTaskLabel(orgId: string, photoId: string): Promise<string> {
  const rows = await db
    .select({ label: shiftChecklistResults.labelSnapshot })
    .from(shiftChecklistResults)
    .where(and(eq(shiftChecklistResults.orgId, orgId), eq(shiftChecklistResults.photoId, photoId)))
    .limit(1);
  return rows[0]?.label ?? 'General cleaning proof';
}

/** Roll up an advisory quality score for a shift from its reviewed photos. */
export async function rollupShiftQuality(orgId: string, shiftIds: string[]) {
  if (!shiftIds.length) return;
  for (const shiftId of shiftIds) {
    const photos = await db
      .select({ aiPass: shiftPhotos.aiPass })
      .from(shiftPhotos)
      .where(and(eq(shiftPhotos.orgId, orgId), eq(shiftPhotos.shiftId, shiftId)));
    const reviewed = photos.filter((p) => p.aiPass != null);
    if (!reviewed.length) continue;
    const passed = reviewed.filter((p) => p.aiPass === true).length;
    const score = Math.round((passed / reviewed.length) * 100);
    await db
      .update(shifts)
      .set({
        aiQualityScore: score,
        aiQualityNotes: `Advisory: ${passed}/${reviewed.length} proof photos look good. Review flags before acting.`,
      })
      .where(and(eq(shifts.orgId, orgId), eq(shifts.id, shiftId)));
  }
}
