import { beforeAll, describe, expect, it } from 'vitest';
import { DateTime } from 'luxon';
import { resetDb } from '@/lib/db/reset';
import { db } from '@/lib/db';
import { recurrences, shifts } from '@/lib/db/schema';
import { and, eq } from 'drizzle-orm';
import { makeOrg, type TestOrg } from './helpers/factory';
import {
  occurrencesBetween,
  createRecurrence,
  materializeRecurrences,
  assignShift,
  createShift,
  markMissedShifts,
} from '@/lib/domain/scheduling';

let O: TestOrg;

beforeAll(async () => {
  await resetDb();
  O = await makeOrg('Sched Org');
});

describe('occurrencesBetween (pure RRULE expansion)', () => {
  it('expands a weekly MO/WE/FR rule to the right local weekday + time', () => {
    const zone = 'America/Chicago';
    const from = DateTime.fromObject({ year: 2026, month: 7, day: 1 }, { zone }).toJSDate();
    const to = DateTime.fromObject({ year: 2026, month: 7, day: 14 }, { zone }).toJSDate();
    const occ = occurrencesBetween('FREQ=WEEKLY;BYDAY=MO,WE,FR', '18:00', zone, from, to);

    // Each occurrence is at 18:00 local and on Mon/Wed/Fri.
    for (const d of occ) {
      const local = DateTime.fromJSDate(d, { zone });
      expect(local.hour).toBe(18);
      expect([1, 3, 5]).toContain(local.weekday); // luxon: Mon=1..Sun=7
    }
    // Jul 1 2026 is a Wednesday; window covers 2 full weeks → 6 occurrences.
    expect(occ.length).toBe(6);
  });
});

describe('materializeRecurrences', () => {
  it('generates upcoming shifts and is idempotent on re-run', async () => {
    const from = DateTime.fromObject({ year: 2026, month: 7, day: 1 }, { zone: 'America/Chicago' }).toJSDate();
    const rec = await createRecurrence(O.orgId, {
      siteId: O.siteId,
      rrule: 'FREQ=WEEKLY;BYDAY=MO,WE,FR',
      defaultAssignedUserId: O.cleanerId,
      startTimeLocal: '18:00',
      durationMinutes: 120,
      active: true,
    });

    // Horizon of 14 days from Wed Jul 1 reaches Jul 15 inclusive → 7 MO/WE/FR.
    const created1 = await materializeRecurrences(O.orgId, 14, from);
    expect(created1).toBe(7);

    // Second run within the same horizon creates nothing new (idempotent).
    const created2 = await materializeRecurrences(O.orgId, 14, from);
    expect(created2).toBe(0);

    const rows = await db
      .select()
      .from(shifts)
      .where(and(eq(shifts.orgId, O.orgId), eq(shifts.recurrenceId, rec.id)));
    expect(rows.length).toBe(7);
    expect(rows.every((r) => r.assignedUserId === O.cleanerId)).toBe(true);
    // durations correct
    for (const r of rows) {
      expect(r.scheduledEnd.getTime() - r.scheduledStart.getTime()).toBe(120 * 60 * 1000);
    }
  });
});

describe('assign / reassign and missed', () => {
  it('reassigns a one-off shift', async () => {
    const start = DateTime.now().plus({ days: 1 }).toJSDate();
    const shift = await createShift(O.orgId, {
      siteId: O.siteId,
      scheduledStart: start,
      scheduledEnd: new Date(start.getTime() + 60 * 60 * 1000),
      assignedUserId: null,
    });
    expect(shift.assignedUserId).toBeNull();
    const updated = await assignShift(O.orgId, shift.id, O.cleanerId);
    expect(updated.assignedUserId).toBe(O.cleanerId);
    const cleared = await assignShift(O.orgId, shift.id, null);
    expect(cleared.assignedUserId).toBeNull();
  });

  it('markMissedShifts flips past scheduled shifts to missed', async () => {
    const past = DateTime.now().minus({ days: 2 }).toJSDate();
    const s = await createShift(O.orgId, {
      siteId: O.siteId,
      scheduledStart: past,
      scheduledEnd: new Date(past.getTime() + 60 * 60 * 1000),
      assignedUserId: O.cleanerId,
    });
    const missed = await markMissedShifts(O.orgId);
    expect(missed).toContain(s.id);
    const after = await db.select().from(shifts).where(eq(shifts.id, s.id));
    expect(after[0].status).toBe('missed');
  });

  it('rejects an invalid RRULE at creation', async () => {
    await expect(
      createRecurrence(O.orgId, {
        siteId: O.siteId,
        rrule: 'NOT_A_RULE',
        startTimeLocal: '18:00',
        durationMinutes: 120,
        active: true,
      }),
    ).rejects.toThrow();
  });
});
