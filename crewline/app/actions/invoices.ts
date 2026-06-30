'use server';
import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { requireOwner } from '@/lib/auth/context';
import {
  generateInvoiceForSite,
  getInvoice,
  markInvoicePaid,
  markInvoiceSent,
} from '@/lib/domain/invoices';
import { getOrg } from '@/lib/domain/org';
import { getClient } from '@/lib/domain/clients';
import { saveOutboundMessage } from '@/lib/domain/messages';
import { sendEmail } from '@/lib/integrations/email';
import { invoiceEmail } from '@/emails/templates';
import { DomainError } from '@/lib/domain/errors';
import type { FormState } from './clients';

export async function generateInvoiceAction(_prev: FormState, formData: FormData): Promise<FormState> {
  const auth = await requireOwner();
  const value = String(formData.get('clientSite') || ''); // "clientId:siteId"
  const [clientId, siteId] = value.split(':');
  const periodStart = String(formData.get('periodStart') || '');
  const periodEnd = String(formData.get('periodEnd') || '');
  const taxDollars = Number(formData.get('taxDollars') || 0);
  if (!clientId || !siteId) return { error: 'Choose a site to invoice.' };
  if (!periodStart || !periodEnd) return { error: 'Choose a billing period.' };

  let invoiceId: string;
  try {
    const inv = await generateInvoiceForSite(auth.orgId, {
      clientId,
      siteId,
      periodStart,
      periodEnd,
      taxCents: Math.round((Number.isFinite(taxDollars) ? taxDollars : 0) * 100),
    });
    invoiceId = inv.id;
  } catch (err) {
    return { error: err instanceof DomainError ? err.message : 'Could not generate invoice.' };
  }
  revalidatePath('/invoices');
  redirect(`/invoices/${invoiceId}`);
}

export async function sendInvoiceAction(_prev: FormState, formData: FormData): Promise<FormState> {
  const auth = await requireOwner();
  const invoiceId = String(formData.get('invoiceId'));
  const toOverride = (formData.get('to') as string) || '';
  const inv = await getInvoice(auth.orgId, invoiceId);
  if (!inv) return { error: 'Invoice not found.' };
  const org = await getOrg(auth.orgId);
  const client = await getClient(auth.orgId, inv.clientId);
  const to = toOverride || client?.contactEmail || '';
  if (!to) return { error: 'No recipient email. Enter one or set the client contact email.' };

  const email = invoiceEmail({
    companyName: org?.name ?? 'Your cleaning company',
    clientName: inv.clientName,
    number: inv.number,
    lineItems: inv.lineItems.map((l) => ({
      description: l.description,
      quantity: Number(l.quantity),
      unitPriceCents: l.unitPriceCents,
      amountCents: l.amountCents,
    })),
    subtotalCents: inv.subtotalCents,
    taxCents: inv.taxCents,
    totalCents: inv.totalCents,
    dueDate: inv.dueDate,
  });
  const res = await sendEmail({ to, subject: email.subject, html: email.html, text: email.text });
  if (!res.ok) return { error: `Email failed (${res.driver}): ${res.error ?? 'unknown'}` };

  await markInvoiceSent(auth.orgId, invoiceId);
  await saveOutboundMessage(auth.orgId, {
    clientId: inv.clientId,
    body: `Invoice ${inv.number} sent to ${to}`,
    channel: 'email',
    status: 'sent',
  });
  revalidatePath(`/invoices/${invoiceId}`);
  revalidatePath('/invoices');
  return { ok: true };
}

export async function markInvoicePaidAction(formData: FormData): Promise<void> {
  const auth = await requireOwner();
  const invoiceId = String(formData.get('invoiceId'));
  await markInvoicePaid(auth.orgId, invoiceId);
  revalidatePath(`/invoices/${invoiceId}`);
  revalidatePath('/invoices');
  revalidatePath('/dashboard');
}
