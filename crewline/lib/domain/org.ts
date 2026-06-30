import { eq } from 'drizzle-orm';
import { db } from '@/lib/db';
import { orgs, orgUsers, users } from '@/lib/db/schema';

export async function getOrg(orgId: string) {
  const rows = await db.select().from(orgs).where(eq(orgs.id, orgId)).limit(1);
  return rows[0] ?? null;
}

export async function getOrgTimezone(orgId: string): Promise<string> {
  const org = await getOrg(orgId);
  return org?.timezone ?? 'America/Chicago';
}

export async function listMembers(orgId: string) {
  return db
    .select({
      id: users.id,
      fullName: users.fullName,
      email: users.email,
      phone: users.phone,
      role: orgUsers.role,
      status: orgUsers.status,
    })
    .from(orgUsers)
    .innerJoin(users, eq(users.id, orgUsers.userId))
    .where(eq(orgUsers.orgId, orgId));
}

export async function inviteMember(
  orgId: string,
  input: { fullName: string; email?: string | null; phone?: string | null; role: 'admin' | 'cleaner' },
) {
  const [user] = await db
    .insert(users)
    .values({ fullName: input.fullName, email: input.email || null, phone: input.phone || null })
    .returning();
  await db
    .insert(orgUsers)
    .values({ orgId, userId: user.id, role: input.role, status: 'invited' });
  return user;
}

