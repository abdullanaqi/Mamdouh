'use server';
import { revalidatePath } from 'next/cache';
import { requireOwner } from '@/lib/auth/context';
import { aiEnabled } from '@/lib/ai/enabled';
import { draftComms } from '@/lib/ai/capabilities';
import { getMessage, saveOutboundMessage } from '@/lib/domain/messages';
import { getClient } from '@/lib/domain/clients';

export type CommsState = { error?: string; ok?: boolean };

/** Draft an AI reply to an inbound client message and save it as a draft. */
export async function draftReplyAction(_prev: CommsState, formData: FormData): Promise<CommsState> {
  const auth = await requireOwner();
  if (!aiEnabled()) {
    return { error: 'AI reply drafting needs ANTHROPIC_API_KEY (see README).' };
  }
  const messageId = String(formData.get('messageId'));
  const inbound = await getMessage(auth.orgId, messageId);
  if (!inbound) return { error: 'Message not found.' };
  const client = inbound.clientId ? await getClient(auth.orgId, inbound.clientId) : null;

  try {
    const draft = await draftComms(auth.orgId, {
      clientName: client?.name,
      inboundMessage: inbound.body,
    });
    await saveOutboundMessage(auth.orgId, {
      clientId: inbound.clientId,
      siteId: inbound.siteId,
      body: draft,
      channel: inbound.channel,
      aiGenerated: true,
      status: 'draft',
    });
    revalidatePath('/messages');
    return { ok: true };
  } catch (err) {
    console.error('draft reply failed:', err);
    return { error: 'Could not draft a reply. Try again.' };
  }
}
