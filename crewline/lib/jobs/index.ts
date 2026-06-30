import { db } from '@/lib/db';
import { orgs } from '@/lib/db/schema';
import { materializeRecurrences, markMissedShifts } from '@/lib/domain/scheduling';
import { aiEnabled } from '@/lib/ai/enabled';
import { checkPhoto } from '@/lib/ai/capabilities';
import { getObject } from '@/lib/integrations/storage';
import {
  listUnreviewedPhotos,
  photoTaskLabel,
  rollupShiftQuality,
  setPhotoReview,
} from '@/lib/domain/photos';
import { and, eq, lt, inArray } from 'drizzle-orm';
import { clients, invoices } from '@/lib/db/schema';
import { getOrg } from '@/lib/domain/org';
import { getInvoice } from '@/lib/domain/invoices';
import { sendEmail } from '@/lib/integrations/email';
import { invoiceEmail } from '@/emails/templates';

/**
 * Background jobs (spec §4). In the MVP these are plain domain functions invoked
 * by cron-guarded API routes (Vercel Cron / any scheduler hitting /api/cron/*).
 * They are pure and unit-testable; swapping to Inngest/Trigger.dev later only
 * changes the transport, not this logic.
 */
async function allOrgIds(): Promise<string[]> {
  const rows = await db.select({ id: orgs.id }).from(orgs);
  return rows.map((r) => r.id);
}

export async function runMaterializeAllOrgs(horizonDays = 14): Promise<number> {
  let total = 0;
  for (const orgId of await allOrgIds()) {
    total += await materializeRecurrences(orgId, horizonDays);
  }
  return total;
}

export async function runMarkMissedAllOrgs(): Promise<number> {
  let total = 0;
  for (const orgId of await allOrgIds()) {
    const ids = await markMissedShifts(orgId);
    total += ids.length;
  }
  return total;
}

function mediaTypeFromKey(key: string): 'image/jpeg' | 'image/png' | 'image/webp' {
  const ext = key.split('.').pop()?.toLowerCase();
  if (ext === 'png') return 'image/png';
  if (ext === 'webp') return 'image/webp';
  return 'image/jpeg';
}

/**
 * Nightly advisory proof-photo quality scan (spec T2.4). Reviews each
 * unreviewed photo with the vision capability and rolls up a per-shift quality
 * score. Advisory only — never penalizes a crew. Skipped if no API key.
 * Returns the number of photos reviewed.
 */
export async function runPhotoQualityScan(): Promise<number> {
  if (!aiEnabled()) return 0;
  let reviewed = 0;
  for (const orgId of await allOrgIds()) {
    const photos = await listUnreviewedPhotos(orgId, 100);
    const touchedShifts = new Set<string>();
    for (const { photo } of photos) {
      const bytes = await getObject(photo.storageKey);
      if (!bytes) continue; // image not on disk (e.g. demo seed) — skip
      try {
        const label = await photoTaskLabel(orgId, photo.id);
        const out = await checkPhoto(orgId, {
          imageBase64: bytes.toString('base64'),
          mediaType: mediaTypeFromKey(photo.storageKey),
          taskLabel: label,
        });
        await setPhotoReview(orgId, photo.id, { pass: out.pass, reason: out.reason });
        touchedShifts.add(photo.shiftId);
        reviewed += 1;
      } catch (err) {
        console.error('photo scan skipped one:', err);
      }
    }
    await rollupShiftQuality(orgId, [...touchedShifts]);
  }
  return reviewed;
}

/**
 * Mark past-due sent invoices overdue and email a reminder (spec T3.3).
 * Returns the number of reminders sent. Uses the email driver (log in dev).
 */
export async function runInvoiceReminders(asOf?: Date): Promise<number> {
  const today = (asOf ?? new Date()).toISOString().slice(0, 10);
  let sent = 0;
  for (const orgId of await allOrgIds()) {
    // Flip sent invoices whose due date has passed to 'overdue'.
    await db
      .update(invoices)
      .set({ status: 'overdue' })
      .where(and(eq(invoices.orgId, orgId), eq(invoices.status, 'sent'), lt(invoices.dueDate, today)));

    const due = await db
      .select({ id: invoices.id, clientId: invoices.clientId })
      .from(invoices)
      .where(and(eq(invoices.orgId, orgId), inArray(invoices.status, ['overdue'])));

    const org = await getOrg(orgId);
    for (const row of due) {
      const inv = await getInvoice(orgId, row.id);
      if (!inv) continue;
      const clientRows = await db
        .select({ email: clients.contactEmail })
        .from(clients)
        .where(and(eq(clients.orgId, orgId), eq(clients.id, row.clientId)))
        .limit(1);
      const to = clientRows[0]?.email;
      if (!to) continue;
      const email = invoiceEmail({
        companyName: org?.name ?? 'Your cleaning company',
        clientName: inv.clientName,
        number: inv.number,
        lineItems: inv.lineItems.map((l) => ({
          description: l.description,
          quantity: Number(l.quantity),
          unitPriceCents: l.unitPriceCents,
          amountCents: l.amountCents,
        })),
        subtotalCents: inv.subtotalCents,
        taxCents: inv.taxCents,
        totalCents: inv.totalCents,
        dueDate: inv.dueDate,
        reminder: true,
      });
      const res = await sendEmail({ to, subject: email.subject, html: email.html, text: email.text });
      if (res.ok) sent += 1;
    }
  }
  return sent;
}
