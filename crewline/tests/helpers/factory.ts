import { db } from '@/lib/db';
import {
  orgs,
  users,
  orgUsers,
  clients,
  sites,
  siteChecklistItems,
} from '@/lib/db/schema';
import { hashPassword } from '@/lib/auth/password';

let counter = 0;
function uniq(prefix: string) {
  counter += 1;
  return `${prefix}-${counter}-${Math.floor(performance.now())}`;
}

export type TestOrg = {
  orgId: string;
  ownerId: string;
  cleanerId: string;
  clientId: string;
  siteId: string;
  checklistItemIds: string[];
};

/** Build a fully-populated org for tests (owner + cleaner + client + site + checklist). */
export async function makeOrg(name = uniq('Org')): Promise<TestOrg> {
  const [org] = await db.insert(orgs).values({ name, timezone: 'America/Chicago' }).returning();

  const [owner] = await db
    .insert(users)
    .values({
      email: `${uniq('owner')}@test.example`,
      fullName: 'Test Owner',
      passwordHash: hashPassword('password123'),
    })
    .returning();

  const [cleaner] = await db
    .insert(users)
    .values({ phone: `+1555${String(1000000 + counter).slice(-7)}`, fullName: 'Test Cleaner' })
    .returning();

  await db.insert(orgUsers).values([
    { orgId: org.id, userId: owner.id, role: 'owner', status: 'active' },
    { orgId: org.id, userId: cleaner.id, role: 'cleaner', status: 'active' },
  ]);

  const [client] = await db
    .insert(clients)
    .values({ orgId: org.id, name: uniq('Client'), billingTerms: 'net30' })
    .returning();

  const [site] = await db
    .insert(sites)
    .values({
      orgId: org.id,
      clientId: client.id,
      name: uniq('Site'),
      address: '1 Test St',
      lat: 41.8781,
      lng: -87.6298,
      geofenceRadiusM: 150,
      siteType: 'office',
      serviceFrequency: 'weekly',
      contractRateCents: 50000,
    })
    .returning();

  const items = await db
    .insert(siteChecklistItems)
    .values([
      { orgId: org.id, siteId: site.id, label: 'Empty trash', requiresPhoto: true, sortOrder: 0 },
      { orgId: org.id, siteId: site.id, label: 'Mop floors', requiresPhoto: false, sortOrder: 1 },
    ])
    .returning();

  return {
    orgId: org.id,
    ownerId: owner.id,
    cleanerId: cleaner.id,
    clientId: client.id,
    siteId: site.id,
    checklistItemIds: items.map((i) => i.id),
  };
}
