import { beforeAll, describe, expect, it } from 'vitest';
import { DateTime } from 'luxon';
import { resetDb } from '@/lib/db/reset';
import { db } from '@/lib/db';
import { shifts } from '@/lib/db/schema';
import { makeOrg, type TestOrg } from './helpers/factory';
import {
  createInvoice,
  generateInvoiceForSite,
  getInvoice,
  listInvoices,
  markInvoicePaid,
  markInvoiceSent,
} from '@/lib/domain/invoices';

let O: TestOrg;
beforeAll(async () => {
  await resetDb();
  O = await makeOrg('Invoice Org');
});

describe('invoice math + numbering', () => {
  it('computes subtotal/tax/total and assigns a unique per-org number', async () => {
    const inv1 = await createInvoice(O.orgId, {
      clientId: O.clientId,
      siteId: O.siteId,
      periodStart: '2026-06-01',
      periodEnd: '2026-06-30',
      lines: [
        { description: 'Nightly cleaning', quantity: 20, unitPriceCents: 8500 },
        { description: 'Carpet shampoo', quantity: 1, unitPriceCents: 25000 },
      ],
      taxCents: 1000,
    });
    // 20*8500 + 25000 = 170000 + 25000 = 195000 subtotal; +1000 tax = 196000.
    expect(inv1.subtotalCents).toBe(195000);
    expect(inv1.taxCents).toBe(1000);
    expect(inv1.totalCents).toBe(196000);
    expect(inv1.number).toBe('INV-0001');
    expect(inv1.status).toBe('draft');

    const inv2 = await createInvoice(O.orgId, {
      clientId: O.clientId,
      lines: [{ description: 'One-off', quantity: 1, unitPriceCents: 5000 }],
    });
    expect(inv2.number).toBe('INV-0002');
  });

  it('generates an invoice from completed shifts at the site contract rate', async () => {
    // Factory site has contractRateCents = 50000. Insert 3 completed shifts.
    const base = DateTime.fromObject({ year: 2026, month: 5, day: 5 }, { zone: 'America/Chicago' });
    for (let i = 0; i < 3; i++) {
      await db.insert(shifts).values({
        orgId: O.orgId,
        siteId: O.siteId,
        assignedUserId: O.cleanerId,
        scheduledStart: base.plus({ days: i }).set({ hour: 18 }).toJSDate(),
        scheduledEnd: base.plus({ days: i }).set({ hour: 20 }).toJSDate(),
        status: 'completed',
      });
    }
    const inv = await generateInvoiceForSite(O.orgId, {
      clientId: O.clientId,
      siteId: O.siteId,
      periodStart: '2026-05-01',
      periodEnd: '2026-05-31',
    });
    const full = await getInvoice(O.orgId, inv.id);
    expect(full?.lineItems.length).toBe(3);
    expect(full?.totalCents).toBe(3 * 50000);
    expect(full?.lineItems.every((l) => l.amountCents === 50000)).toBe(true);
  });

  it('moves through draft → sent → paid', async () => {
    const inv = await createInvoice(O.orgId, {
      clientId: O.clientId,
      lines: [{ description: 'svc', quantity: 1, unitPriceCents: 10000 }],
    });
    const sent = await markInvoiceSent(O.orgId, inv.id);
    expect(sent.status).toBe('sent');
    expect(sent.sentAt).not.toBeNull();
    const paid = await markInvoicePaid(O.orgId, inv.id);
    expect(paid.status).toBe('paid');
    expect(paid.paidAt).not.toBeNull();
  });

  it('is org-scoped', async () => {
    const other = await makeOrg('Other Inv Org');
    const inv = await createInvoice(O.orgId, {
      clientId: O.clientId,
      lines: [{ description: 'svc', quantity: 1, unitPriceCents: 1 }],
    });
    expect(await getInvoice(other.orgId, inv.id)).toBeNull();
    const otherList = await listInvoices(other.orgId);
    expect(otherList.find((i) => i.id === inv.id)).toBeUndefined();
  });
});
