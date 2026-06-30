import { and, desc, eq } from 'drizzle-orm';
import { db } from '@/lib/db';
import { clients, messages } from '@/lib/db/schema';

export async function listMessages(orgId: string) {
  return db
    .select({ message: messages, clientName: clients.name })
    .from(messages)
    .leftJoin(clients, eq(clients.id, messages.clientId))
    .where(eq(messages.orgId, orgId))
    .orderBy(desc(messages.createdAt));
}

export async function getMessage(orgId: string, messageId: string) {
  const rows = await db
    .select()
    .from(messages)
    .where(and(eq(messages.orgId, orgId), eq(messages.id, messageId)))
    .limit(1);
  return rows[0] ?? null;
}

export async function saveOutboundMessage(
  orgId: string,
  input: { clientId?: string | null; siteId?: string | null; body: string; channel?: string; aiGenerated?: boolean; status?: string },
) {
  const rows = await db
    .insert(messages)
    .values({
      orgId,
      clientId: input.clientId ?? null,
      siteId: input.siteId ?? null,
      direction: 'outbound',
      channel: input.channel ?? 'email',
      body: input.body,
      aiGenerated: input.aiGenerated ?? false,
      status: input.status ?? 'draft',
    })
    .returning();
  return rows[0];
}
