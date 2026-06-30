'use server';
import { revalidatePath } from 'next/cache';
import { requireOwner } from '@/lib/auth/context';
import { setIssueStatus } from '@/lib/domain/issues';

export async function setIssueStatusAction(formData: FormData): Promise<void> {
  const auth = await requireOwner();
  const issueId = String(formData.get('issueId'));
  const status = String(formData.get('status')) as 'open' | 'acknowledged' | 'resolved';
  await setIssueStatus(auth.orgId, issueId, status);
  revalidatePath('/messages');
}
