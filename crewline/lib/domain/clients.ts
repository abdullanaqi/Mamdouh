import { and, desc, eq } from 'drizzle-orm';
import { db } from '@/lib/db';
import { clients, sites } from '@/lib/db/schema';
import { notFound } from './errors';
import { clientInput, type ClientInput } from './schemas';

/**
 * Clients domain — every function is scoped by org_id (primary tenancy guard).
 */
export async function listClients(orgId: string) {
  return db
    .select()
    .from(clients)
    .where(eq(clients.orgId, orgId))
    .orderBy(desc(clients.createdAt));
}

export async function getClient(orgId: string, clientId: string) {
  const rows = await db
    .select()
    .from(clients)
    .where(and(eq(clients.orgId, orgId), eq(clients.id, clientId)))
    .limit(1);
  return rows[0] ?? null;
}

export async function createClient(orgId: string, input: ClientInput) {
  const data = clientInput.parse(input);
  const rows = await db
    .insert(clients)
    .values({
      orgId,
      name: data.name,
      contactName: data.contactName ?? null,
      contactEmail: data.contactEmail || null,
      contactPhone: data.contactPhone ?? null,
      billingTerms: data.billingTerms,
      notes: data.notes ?? null,
    })
    .returning();
  return rows[0];
}

export async function updateClient(orgId: string, clientId: string, input: ClientInput) {
  const data = clientInput.parse(input);
  const rows = await db
    .update(clients)
    .set({
      name: data.name,
      contactName: data.contactName ?? null,
      contactEmail: data.contactEmail || null,
      contactPhone: data.contactPhone ?? null,
      billingTerms: data.billingTerms,
      notes: data.notes ?? null,
    })
    .where(and(eq(clients.orgId, orgId), eq(clients.id, clientId)))
    .returning();
  if (!rows[0]) notFound('Client');
  return rows[0];
}

export async function listClientsWithSiteCount(orgId: string) {
  const cs = await listClients(orgId);
  const siteRows = await db
    .select({ clientId: sites.clientId })
    .from(sites)
    .where(eq(sites.orgId, orgId));
  const counts = new Map<string, number>();
  for (const s of siteRows) counts.set(s.clientId, (counts.get(s.clientId) ?? 0) + 1);
  return cs.map((c) => ({ ...c, siteCount: counts.get(c.id) ?? 0 }));
}
