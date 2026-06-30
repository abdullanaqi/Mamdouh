import { and, asc, desc, eq } from 'drizzle-orm';
import { db } from '@/lib/db';
import { clients, siteChecklistItems, sites } from '@/lib/db/schema';
import { geocodeAddress } from '@/lib/integrations/maps';
import { notFound } from './errors';
import { siteInput, checklistItemInput, type SiteInput, type ChecklistItemInput } from './schemas';

export async function listSites(orgId: string) {
  return db
    .select({
      site: sites,
      clientName: clients.name,
    })
    .from(sites)
    .innerJoin(clients, eq(clients.id, sites.clientId))
    .where(eq(sites.orgId, orgId))
    .orderBy(desc(sites.createdAt));
}

export async function listSitesForClient(orgId: string, clientId: string) {
  return db
    .select()
    .from(sites)
    .where(and(eq(sites.orgId, orgId), eq(sites.clientId, clientId)))
    .orderBy(desc(sites.createdAt));
}

export async function getSite(orgId: string, siteId: string) {
  const rows = await db
    .select()
    .from(sites)
    .where(and(eq(sites.orgId, orgId), eq(sites.id, siteId)))
    .limit(1);
  return rows[0] ?? null;
}

export async function getSiteWithChecklist(orgId: string, siteId: string) {
  const site = await getSite(orgId, siteId);
  if (!site) return null;
  const checklist = await listChecklist(orgId, siteId);
  return { ...site, checklist };
}

export async function listChecklist(orgId: string, siteId: string) {
  return db
    .select()
    .from(siteChecklistItems)
    .where(and(eq(siteChecklistItems.orgId, orgId), eq(siteChecklistItems.siteId, siteId)))
    .orderBy(asc(siteChecklistItems.sortOrder));
}

/** Verify a client belongs to this org before linking a site to it. */
async function assertClientInOrg(orgId: string, clientId: string) {
  const rows = await db
    .select({ id: clients.id })
    .from(clients)
    .where(and(eq(clients.orgId, orgId), eq(clients.id, clientId)))
    .limit(1);
  if (!rows[0]) notFound('Client');
}

export async function createSite(orgId: string, input: SiteInput) {
  const data = siteInput.parse(input);
  await assertClientInOrg(orgId, data.clientId);

  // Geocode if coordinates weren't supplied and a driver is configured.
  let { lat, lng } = data;
  if ((lat == null || lng == null) && data.address) {
    const geo = await geocodeAddress(data.address);
    if (geo) {
      lat = geo.lat;
      lng = geo.lng;
    }
  }

  const inserted = await db
    .insert(sites)
    .values({
      orgId,
      clientId: data.clientId,
      name: data.name,
      address: data.address ?? null,
      lat: lat ?? null,
      lng: lng ?? null,
      geofenceRadiusM: data.geofenceRadiusM,
      siteType: data.siteType,
      squareFootage: data.squareFootage ?? null,
      serviceFrequency: data.serviceFrequency,
      contractRateCents: data.contractRateCents ?? null,
      active: data.active,
    })
    .returning();
  const site = inserted[0];

  if (data.checklist?.length) {
    await db.insert(siteChecklistItems).values(
      data.checklist.map((c, i) => ({
        orgId,
        siteId: site.id,
        label: c.label,
        area: c.area ?? null,
        requiresPhoto: c.requiresPhoto,
        sortOrder: c.sortOrder || i,
      })),
    );
  }
  return site;
}

export async function updateSite(orgId: string, siteId: string, input: SiteInput) {
  const data = siteInput.parse(input);
  await assertClientInOrg(orgId, data.clientId);

  let { lat, lng } = data;
  if ((lat == null || lng == null) && data.address) {
    const geo = await geocodeAddress(data.address);
    if (geo) {
      lat = geo.lat;
      lng = geo.lng;
    }
  }

  const rows = await db
    .update(sites)
    .set({
      clientId: data.clientId,
      name: data.name,
      address: data.address ?? null,
      lat: lat ?? null,
      lng: lng ?? null,
      geofenceRadiusM: data.geofenceRadiusM,
      siteType: data.siteType,
      squareFootage: data.squareFootage ?? null,
      serviceFrequency: data.serviceFrequency,
      contractRateCents: data.contractRateCents ?? null,
      active: data.active,
    })
    .where(and(eq(sites.orgId, orgId), eq(sites.id, siteId)))
    .returning();
  if (!rows[0]) notFound('Site');
  return rows[0];
}

export async function replaceChecklist(
  orgId: string,
  siteId: string,
  items: ChecklistItemInput[],
) {
  const site = await getSite(orgId, siteId);
  if (!site) notFound('Site');
  const parsed = items.map((i) => checklistItemInput.parse(i));
  await db
    .delete(siteChecklistItems)
    .where(and(eq(siteChecklistItems.orgId, orgId), eq(siteChecklistItems.siteId, siteId)));
  if (parsed.length) {
    await db.insert(siteChecklistItems).values(
      parsed.map((c, i) => ({
        orgId,
        siteId,
        label: c.label,
        area: c.area ?? null,
        requiresPhoto: c.requiresPhoto,
        sortOrder: c.sortOrder || i,
      })),
    );
  }
  return listChecklist(orgId, siteId);
}
