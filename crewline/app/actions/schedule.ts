'use server';
import { revalidatePath } from 'next/cache';
import { requireOwner } from '@/lib/auth/context';
import {
  assignShift,
  cancelShift,
  createRecurrence,
  createShift,
  materializeRecurrences,
} from '@/lib/domain/scheduling';
import { DomainError } from '@/lib/domain/errors';
import type { FormState } from './clients';

/** Build an RRULE string from simple weekly form inputs. */
function buildWeeklyRrule(days: string[]): string {
  const map: Record<string, string> = {
    mon: 'MO', tue: 'TU', wed: 'WE', thu: 'TH', fri: 'FR', sat: 'SA', sun: 'SU',
  };
  const byday = days.map((d) => map[d]).filter(Boolean);
  if (!byday.length) throw new DomainError('Select at least one weekday');
  return `FREQ=WEEKLY;BYDAY=${byday.join(',')}`;
}

export async function createOneOffShiftAction(
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const auth = await requireOwner();
  try {
    const date = String(formData.get('date'));
    const time = String(formData.get('time') || '18:00');
    const durationMin = Number(formData.get('durationMinutes') || 120);
    const start = new Date(`${date}T${time}:00`);
    if (Number.isNaN(start.getTime())) throw new DomainError('Invalid date/time');
    const assignedUserId = (formData.get('assignedUserId') as string) || null;
    const shift = await createShift(auth.orgId, {
      siteId: String(formData.get('siteId')),
      assignedUserId: assignedUserId || null,
      scheduledStart: start,
      scheduledEnd: new Date(start.getTime() + durationMin * 60_000),
    });
    revalidatePath('/schedule');
    return { ok: true, id: shift.id };
  } catch (err) {
    return { error: err instanceof DomainError ? err.message : 'Could not create shift.' };
  }
}

export async function createRecurrenceAction(
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const auth = await requireOwner();
  try {
    const days = formData.getAll('weekday').map(String);
    const rrule = buildWeeklyRrule(days);
    const rec = await createRecurrence(auth.orgId, {
      siteId: String(formData.get('siteId')),
      rrule,
      defaultAssignedUserId: (formData.get('assignedUserId') as string) || null,
      startTimeLocal: String(formData.get('time') || '18:00'),
      durationMinutes: Number(formData.get('durationMinutes') || 120),
      active: true,
    });
    // Immediately materialize the next two weeks so the schedule isn't empty.
    await materializeRecurrences(auth.orgId, 14);
    revalidatePath('/schedule');
    return { ok: true, id: rec.id };
  } catch (err) {
    return { error: err instanceof DomainError ? err.message : 'Could not create recurrence.' };
  }
}

export async function assignShiftAction(formData: FormData): Promise<void> {
  const auth = await requireOwner();
  const shiftId = String(formData.get('shiftId'));
  const userId = (formData.get('assignedUserId') as string) || null;
  await assignShift(auth.orgId, shiftId, userId || null);
  revalidatePath('/schedule');
  revalidatePath(`/shifts/${shiftId}`);
}

export async function cancelShiftAction(formData: FormData): Promise<void> {
  const auth = await requireOwner();
  const shiftId = String(formData.get('shiftId'));
  await cancelShift(auth.orgId, shiftId);
  revalidatePath('/schedule');
}

export async function materializeNowAction(): Promise<FormState> {
  const auth = await requireOwner();
  const created = await materializeRecurrences(auth.orgId, 14);
  revalidatePath('/schedule');
  return { ok: true, id: String(created) };
}
