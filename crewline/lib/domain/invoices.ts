import { and, desc, eq, gte, lte, sql as dsql } from 'drizzle-orm';
import { DateTime } from 'luxon';
import { db } from '@/lib/db';
import {
  clients,
  invoiceLineItems,
  invoices,
  shifts,
  sites,
} from '@/lib/db/schema';
import { getOrgTimezone } from './org';
import { DomainError, notFound } from './errors';

export async function listInvoices(orgId: string) {
  const rows = await db
    .select({ invoice: invoices, clientName: clients.name })
    .from(invoices)
    .innerJoin(clients, eq(clients.id, invoices.clientId))
    .where(eq(invoices.orgId, orgId))
    .orderBy(desc(invoices.createdAt));
  return rows.map((r) => ({ ...r.invoice, clientName: r.clientName }));
}

export async function getInvoice(orgId: string, invoiceId: string) {
  const rows = await db
    .select({ invoice: invoices, clientName: clients.name, clientEmail: clients.contactEmail })
    .from(invoices)
    .innerJoin(clients, eq(clients.id, invoices.clientId))
    .where(and(eq(invoices.orgId, orgId), eq(invoices.id, invoiceId)))
    .limit(1);
  if (!rows[0]) return null;
  const lines = await db
    .select()
    .from(invoiceLineItems)
    .where(and(eq(invoiceLineItems.orgId, orgId), eq(invoiceLineItems.invoiceId, invoiceId)));
  return { ...rows[0].invoice, clientName: rows[0].clientName, clientEmail: rows[0].clientEmail, lineItems: lines };
}

/** Next per-org invoice number, e.g. INV-0001. Unique index enforces no dupes. */
async function nextInvoiceNumber(orgId: string): Promise<string> {
  const rows = await db
    .select({ count: dsql<number>`count(*)::int` })
    .from(invoices)
    .where(eq(invoices.orgId, orgId));
  const n = (rows[0]?.count ?? 0) + 1;
  return `INV-${String(n).padStart(4, '0')}`;
}

export type InvoiceDraftLine = {
  description: string;
  quantity: number;
  unitPriceCents: number;
};

/**
 * Generate an invoice for a client/site from completed shifts in a period.
 * Each completed shift becomes a line item priced at the site's contract rate.
 * Falls back to a single manual line if no completed shifts / no rate.
 */
export async function generateInvoiceForSite(
  orgId: string,
  args: { clientId: string; siteId: string; periodStart: string; periodEnd: string; taxCents?: number },
) {
  const tz = await getOrgTimezone(orgId);
  const from = DateTime.fromISO(args.periodStart, { zone: tz }).startOf('day').toJSDate();
  const to = DateTime.fromISO(args.periodEnd, { zone: tz }).endOf('day').toJSDate();

  const siteRows = await db
    .select()
    .from(sites)
    .where(and(eq(sites.orgId, orgId), eq(sites.id, args.siteId), eq(sites.clientId, args.clientId)))
    .limit(1);
  const site = siteRows[0];
  if (!site) notFound('Site');

  const completed = await db
    .select()
    .from(shifts)
    .where(
      and(
        eq(shifts.orgId, orgId),
        eq(shifts.siteId, args.siteId),
        eq(shifts.status, 'completed'),
        gte(shifts.scheduledStart, from),
        lte(shifts.scheduledStart, to),
      ),
    );

  const rate = site.contractRateCents ?? 0;
  const lines: InvoiceDraftLine[] =
    completed.length > 0 && rate > 0
      ? completed.map((s) => ({
          description: `Cleaning service — ${site.name} (${DateTime.fromJSDate(s.scheduledStart, { zone: tz }).toFormat('LLL d')})`,
          quantity: 1,
          unitPriceCents: rate,
        }))
      : [
          {
            description: `Cleaning service — ${site.name} (${args.periodStart} to ${args.periodEnd})`,
            quantity: 1,
            unitPriceCents: rate || 0,
          },
        ];

  return createInvoice(orgId, {
    clientId: args.clientId,
    siteId: args.siteId,
    periodStart: args.periodStart,
    periodEnd: args.periodEnd,
    lines,
    taxCents: args.taxCents ?? 0,
  });
}

export async function createInvoice(
  orgId: string,
  args: {
    clientId: string;
    siteId?: string | null;
    periodStart?: string | null;
    periodEnd?: string | null;
    lines: InvoiceDraftLine[];
    taxCents?: number;
  },
) {
  if (!args.lines.length) throw new DomainError('An invoice needs at least one line item');

  // Verify the client belongs to the org.
  const c = await db
    .select({ id: clients.id, billingTerms: clients.billingTerms })
    .from(clients)
    .where(and(eq(clients.orgId, orgId), eq(clients.id, args.clientId)))
    .limit(1);
  if (!c[0]) notFound('Client');

  const subtotal = args.lines.reduce((acc, l) => acc + Math.round(l.quantity * l.unitPriceCents), 0);
  const tax = args.taxCents ?? 0;
  const total = subtotal + tax;
  const number = await nextInvoiceNumber(orgId);

  const dueDays = c[0].billingTerms === 'net15' ? 15 : c[0].billingTerms === 'net30' ? 30 : 0;
  const dueDate = args.periodEnd
    ? DateTime.fromISO(args.periodEnd).plus({ days: dueDays }).toISODate()
    : DateTime.now().plus({ days: dueDays }).toISODate();

  return db.transaction(async (tx) => {
    const [inv] = await tx
      .insert(invoices)
      .values({
        orgId,
        clientId: args.clientId,
        siteId: args.siteId ?? null,
        number,
        periodStart: args.periodStart ?? null,
        periodEnd: args.periodEnd ?? null,
        subtotalCents: subtotal,
        taxCents: tax,
        totalCents: total,
        status: 'draft',
        dueDate,
      })
      .returning();

    await tx.insert(invoiceLineItems).values(
      args.lines.map((l) => ({
        orgId,
        invoiceId: inv.id,
        description: l.description,
        quantity: String(l.quantity),
        unitPriceCents: l.unitPriceCents,
        amountCents: Math.round(l.quantity * l.unitPriceCents),
      })),
    );
    return inv;
  });
}

export async function markInvoiceSent(orgId: string, invoiceId: string) {
  const rows = await db
    .update(invoices)
    .set({ status: 'sent', sentAt: new Date() })
    .where(and(eq(invoices.orgId, orgId), eq(invoices.id, invoiceId)))
    .returning();
  if (!rows[0]) notFound('Invoice');
  return rows[0];
}

export async function markInvoicePaid(orgId: string, invoiceId: string) {
  const rows = await db
    .update(invoices)
    .set({ status: 'paid', paidAt: new Date() })
    .where(and(eq(invoices.orgId, orgId), eq(invoices.id, invoiceId)))
    .returning();
  if (!rows[0]) notFound('Invoice');
  return rows[0];
}

export async function listSitesForInvoicing(orgId: string) {
  return db
    .select({ siteId: sites.id, siteName: sites.name, clientId: sites.clientId, clientName: clients.name })
    .from(sites)
    .innerJoin(clients, eq(clients.id, sites.clientId))
    .where(and(eq(sites.orgId, orgId), eq(sites.active, true)));
}
