'use server';
import { revalidatePath } from 'next/cache';
import { requireOwner } from '@/lib/auth/context';
import { inviteMember } from '@/lib/domain/org';
import type { FormState } from './clients';

export async function inviteMemberAction(_prev: FormState, formData: FormData): Promise<FormState> {
  const auth = await requireOwner();
  const fullName = String(formData.get('fullName') || '').trim();
  const role = String(formData.get('role') || 'cleaner') as 'admin' | 'cleaner';
  const email = (formData.get('email') as string) || null;
  const phone = (formData.get('phone') as string) || null;
  if (!fullName) return { error: 'Name is required.' };
  if (role === 'cleaner' && !phone) return { error: 'Crew need a phone number for OTP login.' };
  if (role === 'admin' && !email) return { error: 'Admins need an email to sign in.' };
  await inviteMember(auth.orgId, { fullName, email, phone, role });
  revalidatePath('/settings');
  return { ok: true };
}
