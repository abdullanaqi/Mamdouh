import { and, desc, eq } from 'drizzle-orm';
import { db } from '@/lib/db';
import { clients, invoices } from '@/lib/db/schema';

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
    .select()
    .from(invoices)
    .where(and(eq(invoices.orgId, orgId), eq(invoices.id, invoiceId)))
    .limit(1);
  return rows[0] ?? null;
}
