'use server';
import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { requireOwner } from '@/lib/auth/context';
import { aiEnabled } from '@/lib/ai/enabled';
import { generateQuote, writeProposal } from '@/lib/ai/capabilities';
import { quoteInputSchema } from '@/lib/ai/schemas';
import {
  createQuote,
  pastQuotesForCalibration,
  setQuoteStatus,
  updateQuoteProposal,
} from '@/lib/domain/quotes';
import { getOrg } from '@/lib/domain/org';
import { getClient } from '@/lib/domain/clients';
import type { FormState } from './clients';

function num(v: FormDataEntryValue | null): number | undefined {
  if (v == null || v === '') return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

export async function generateQuoteAction(_prev: FormState, formData: FormData): Promise<FormState> {
  const auth = await requireOwner();
  if (!aiEnabled()) {
    return {
      error:
        'AI quote generation needs ANTHROPIC_API_KEY. Add it to your environment (see README), then try again.',
    };
  }

  const clientId = (formData.get('clientId') as string) || null;
  const prospectName = (formData.get('prospectName') as string) || null;

  let quoteId: string;
  try {
    const input = quoteInputSchema.parse({
      siteType: formData.get('siteType') || 'office',
      squareFootage: num(formData.get('squareFootage')),
      frequency: formData.get('frequency') || 'monthly',
      scopeNotes: String(formData.get('scopeNotes') || ''),
      region: (formData.get('region') as string) || undefined,
      ownerPricingHints: (formData.get('ownerPricingHints') as string) || undefined,
      pastQuotes: await pastQuotesForCalibration(auth.orgId),
    });

    const ai = await generateQuote(auth.orgId, input);

    const org = await getOrg(auth.orgId);
    const client = clientId ? await getClient(auth.orgId, clientId) : null;
    const proposal = await writeProposal(auth.orgId, {
      companyName: org?.name ?? 'Our company',
      clientName: client?.name ?? prospectName ?? 'the client',
      siteType: input.siteType,
      frequency: input.frequency,
      totalCents: ai.totalCents,
      lineItems: ai.lineItems.map((li) => ({
        description: li.description,
        area: li.area,
        amountCents: li.amountCents,
      })),
    });

    const quote = await createQuote(auth.orgId, {
      clientId,
      prospectName,
      frequency: input.frequency,
      inputsJson: input,
      lineItems: ai.lineItems,
      totalCents: ai.totalCents,
      proposalText: proposal,
    });
    quoteId = quote.id;
  } catch (err) {
    console.error('quote generation failed:', err);
    return { error: 'Quote generation failed. Please try again.' };
  }

  revalidatePath('/quotes');
  redirect(`/quotes/${quoteId}`);
}

export async function saveProposalAction(_prev: FormState, formData: FormData): Promise<FormState> {
  const auth = await requireOwner();
  const quoteId = String(formData.get('quoteId'));
  await updateQuoteProposal(auth.orgId, quoteId, String(formData.get('proposalText') || ''));
  revalidatePath(`/quotes/${quoteId}`);
  return { ok: true };
}

export async function setQuoteStatusAction(formData: FormData): Promise<void> {
  const auth = await requireOwner();
  const quoteId = String(formData.get('quoteId'));
  const status = String(formData.get('status')) as 'draft' | 'sent' | 'won' | 'lost';
  await setQuoteStatus(auth.orgId, quoteId, status);
  revalidatePath(`/quotes/${quoteId}`);
  revalidatePath('/quotes');
}
