import { and, desc, eq } from 'drizzle-orm';
import { db } from '@/lib/db';
import { clients, quotes } from '@/lib/db/schema';
import { notFound } from './errors';
import { quoteStatusEnum } from './schemas';

export type QuoteLineItem = {
  description: string;
  area?: string;
  unitBasis: string;
  quantity: number;
  unitPriceCents: number;
  amountCents: number;
};

export async function createQuote(
  orgId: string,
  input: {
    clientId?: string | null;
    siteId?: string | null;
    prospectName?: string | null;
    frequency: string;
    inputsJson: unknown;
    lineItems: QuoteLineItem[];
    totalCents: number;
    proposalText?: string | null;
  },
) {
  const rows = await db
    .insert(quotes)
    .values({
      orgId,
      clientId: input.clientId ?? null,
      siteId: input.siteId ?? null,
      prospectName: input.prospectName ?? null,
      frequency: input.frequency,
      inputsJson: input.inputsJson as any,
      lineItemsJson: input.lineItems as any,
      totalCents: input.totalCents,
      proposalText: input.proposalText ?? null,
      status: 'draft',
    })
    .returning();
  return rows[0];
}

export async function updateQuoteProposal(orgId: string, quoteId: string, proposalText: string) {
  const rows = await db
    .update(quotes)
    .set({ proposalText })
    .where(and(eq(quotes.orgId, orgId), eq(quotes.id, quoteId)))
    .returning();
  if (!rows[0]) notFound('Quote');
  return rows[0];
}

export async function setQuoteStatus(
  orgId: string,
  quoteId: string,
  status: 'draft' | 'sent' | 'won' | 'lost',
) {
  const rows = await db
    .update(quotes)
    .set({ status: quoteStatusEnum.parse(status) })
    .where(and(eq(quotes.orgId, orgId), eq(quotes.id, quoteId)))
    .returning();
  if (!rows[0]) notFound('Quote');
  return rows[0];
}

/** Past won/lost quotes for few-shot calibration (the data moat — spec §6.2A). */
export async function pastQuotesForCalibration(orgId: string, limit = 5) {
  const rows = await db
    .select()
    .from(quotes)
    .where(and(eq(quotes.orgId, orgId)))
    .orderBy(desc(quotes.createdAt))
    .limit(50);
  return rows
    .filter((q) => q.status === 'won' || q.status === 'lost')
    .slice(0, limit)
    .map((q) => ({ inputs: q.inputsJson, total: q.totalCents, won: q.status === 'won' }));
}

export async function listQuotes(orgId: string) {
  const rows = await db
    .select({ quote: quotes, clientName: clients.name })
    .from(quotes)
    .leftJoin(clients, eq(clients.id, quotes.clientId))
    .where(eq(quotes.orgId, orgId))
    .orderBy(desc(quotes.createdAt));
  return rows.map((r) => ({ ...r.quote, clientName: r.clientName }));
}

export async function getQuote(orgId: string, quoteId: string) {
  const rows = await db
    .select()
    .from(quotes)
    .where(and(eq(quotes.orgId, orgId), eq(quotes.id, quoteId)))
    .limit(1);
  return rows[0] ?? null;
}
