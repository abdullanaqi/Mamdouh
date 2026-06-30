'use server';
import { revalidatePath } from 'next/cache';
import { requireCrew } from '@/lib/auth/context';
import { addShiftPhoto } from '@/lib/domain/shifts';
import { putObject } from '@/lib/integrations/storage';
import { DomainError } from '@/lib/domain/errors';

export type CrewActionState = { error?: string; ok?: boolean };

/**
 * Proof-photo upload. Online-only (binary uploads aren't queued offline); the
 * offline queue covers clock-in/out, checklist, and issues via /api/crew/*.
 */
export async function uploadPhotoAction(
  _prev: CrewActionState,
  formData: FormData,
): Promise<CrewActionState> {
  const auth = await requireCrew();
  const shiftId = String(formData.get('shiftId'));
  const resultId = (formData.get('resultId') as string) || null;
  const caption = (formData.get('caption') as string) || null;
  const file = formData.get('photo');
  if (!(file instanceof File) || file.size === 0) {
    return { error: 'Choose a photo to upload.' };
  }
  try {
    const bytes = Buffer.from(await file.arrayBuffer());
    const stored = await putObject(auth.orgId, bytes, file.type || 'image/jpeg');
    await addShiftPhoto(auth.orgId, shiftId, auth.userId, {
      storageKey: stored.key,
      url: stored.url,
      caption,
      resultId,
    });
    revalidatePath(`/shift/${shiftId}`);
    return { ok: true };
  } catch (err) {
    return { error: err instanceof DomainError ? err.message : 'Could not upload photo.' };
  }
}
