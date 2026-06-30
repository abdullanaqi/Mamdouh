import { beforeAll, describe, expect, it } from 'vitest';
import { sql } from '@/lib/db';
import { resetDb } from '@/lib/db/reset';
import { makeOrg, type TestOrg } from './helpers/factory';
import { getClient, listClients } from '@/lib/domain/clients';
import { getSite, listSites } from '@/lib/domain/sites';

/**
 * Release-blocking tenancy isolation (spec T0.3 AC + §5.3).
 * Two layers are proven:
 *   1. Data-access layer scoping (the runtime guarantee).
 *   2. Postgres RLS enforcement when the connection acts as the restricted
 *      `crewline_app` role with the `app.current_org_id` GUC set.
 */
let A: TestOrg;
let B: TestOrg;

beforeAll(async () => {
  await resetDb();
  A = await makeOrg('Org A');
  B = await makeOrg('Org B');
});

describe('data-access layer org scoping', () => {
  it('listClients only returns the caller org rows', async () => {
    const aClients = await listClients(A.orgId);
    const bClients = await listClients(B.orgId);
    expect(aClients.map((c) => c.id)).toContain(A.clientId);
    expect(aClients.map((c) => c.id)).not.toContain(B.clientId);
    expect(bClients.map((c) => c.id)).toContain(B.clientId);
    expect(bClients.map((c) => c.id)).not.toContain(A.clientId);
  });

  it('getClient cannot fetch another org row by id', async () => {
    const crossOrg = await getClient(A.orgId, B.clientId);
    expect(crossOrg).toBeNull();
    const sameOrg = await getClient(A.orgId, A.clientId);
    expect(sameOrg?.id).toBe(A.clientId);
  });

  it('listSites / getSite are org-scoped', async () => {
    const aSites = await listSites(A.orgId);
    expect(aSites.map((s) => s.site.id)).toContain(A.siteId);
    expect(aSites.map((s) => s.site.id)).not.toContain(B.siteId);
    expect(await getSite(A.orgId, B.siteId)).toBeNull();
  });
});

describe('Postgres RLS enforcement (crewline_app role + GUC)', () => {
  it('blocks reads of other orgs even with a direct query', async () => {
    // Acting as the restricted role with org A selected, only A's rows are visible.
    const aRows = await sql.begin(async (tx) => {
      await tx.unsafe(`SET LOCAL ROLE crewline_app`);
      await tx.unsafe(`SET LOCAL app.current_org_id = '${A.orgId}'`);
      return tx.unsafe(`SELECT id, org_id FROM clients`);
    });
    const aIds = aRows.map((r: any) => r.id);
    expect(aIds).toContain(A.clientId);
    expect(aIds).not.toContain(B.clientId);

    // Switch to org B: now only B's rows are visible.
    const bRows = await sql.begin(async (tx) => {
      await tx.unsafe(`SET LOCAL ROLE crewline_app`);
      await tx.unsafe(`SET LOCAL app.current_org_id = '${B.orgId}'`);
      return tx.unsafe(`SELECT id, org_id FROM clients`);
    });
    const bIds = bRows.map((r: any) => r.id);
    expect(bIds).toContain(B.clientId);
    expect(bIds).not.toContain(A.clientId);
  });

  it('WITH CHECK blocks inserting a row for a different org', async () => {
    await expect(
      sql.begin(async (tx) => {
        await tx.unsafe(`SET LOCAL ROLE crewline_app`);
        await tx.unsafe(`SET LOCAL app.current_org_id = '${A.orgId}'`);
        // Try to smuggle in a row tagged with org B while scoped to org A.
        await tx.unsafe(
          `INSERT INTO clients (org_id, name, billing_terms) VALUES ('${B.orgId}', 'evil', 'net30')`,
        );
      }),
    ).rejects.toThrow();
  });

  it('with no GUC set, the restricted role sees nothing', async () => {
    const rows = await sql.begin(async (tx) => {
      await tx.unsafe(`SET LOCAL ROLE crewline_app`);
      return tx.unsafe(`SELECT id FROM clients`);
    });
    expect(rows.length).toBe(0);
  });
});
