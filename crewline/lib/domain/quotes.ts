import { and, desc, eq } from 'drizzle-orm';
import { db } from '@/lib/db';
import { clients, quotes } from '@/lib/db/schema';

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
