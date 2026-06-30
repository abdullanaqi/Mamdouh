'use server';
import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { requireOwner } from '@/lib/auth/context';
import { aiEnabled } from '@/lib/ai/enabled';
import { generateQuote, writeProposal } from '@/lib/ai/capabilities';
import { quoteInputSchema } from '@/lib/ai/schemas';
import {
  createQuote,
  getQuote,
  pastQuotesForCalibration,
  setQuoteStatus,
  updateQuoteProposal,
  type QuoteLineItem,
} from '@/lib/domain/quotes';
import { getOrg } from '@/lib/domain/org';
import { getClient } from '@/lib/domain/clients';
import { saveOutboundMessage } from '@/lib/domain/messages';
import { sendEmail } from '@/lib/integrations/email';
import { quoteEmail } from '@/emails/templates';
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

/** Send the proposal to the client by email, then mark the quote sent. */
export async function sendQuoteAction(_prev: FormState, formData: FormData): Promise<FormState> {
  const auth = await requireOwner();
  const quoteId = String(formData.get('quoteId'));
  const toOverride = (formData.get('to') as string) || '';
  const quote = await getQuote(auth.orgId, quoteId);
  if (!quote) return { error: 'Quote not found.' };

  const org = await getOrg(auth.orgId);
  const client = quote.clientId ? await getClient(auth.orgId, quote.clientId) : null;
  const to = toOverride || client?.contactEmail || '';
  if (!to) return { error: 'No recipient email. Enter one or set the client contact email.' };

  const lineItems = (quote.lineItemsJson as QuoteLineItem[] | null) ?? [];
  const email = quoteEmail({
    companyName: org?.name ?? 'Your cleaning company',
    clientName: client?.name ?? quote.prospectName ?? 'there',
    proposalText: quote.proposalText ?? 'Please see the attached proposal.',
    lineItems: lineItems.map((li) => ({ description: li.description, amountCents: li.amountCents })),
    totalCents: quote.totalCents,
    frequency: quote.frequency,
  });

  const res = await sendEmail({ to, subject: email.subject, html: email.html, text: email.text });
  if (!res.ok) return { error: `Email failed (${res.driver}): ${res.error ?? 'unknown'}` };

  await setQuoteStatus(auth.orgId, quoteId, 'sent');
  await saveOutboundMessage(auth.orgId, {
    clientId: quote.clientId,
    body: `Quote ${formatTotal(quote.totalCents)} sent to ${to}`,
    channel: 'email',
    status: 'sent',
  });
  revalidatePath(`/quotes/${quoteId}`);
  return { ok: true };
}

function formatTotal(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}
