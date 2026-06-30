import { beforeAll, describe, expect, it } from 'vitest';
import { DateTime } from 'luxon';
import { resetDb } from '@/lib/db/reset';
import { makeOrg, type TestOrg } from './helpers/factory';
import { createShift } from '@/lib/domain/scheduling';
import {
  clockIn,
  clockOut,
  getShiftDetail,
  setChecklistResult,
  addShiftPhoto,
  getCrewToday,
} from '@/lib/domain/shifts';
import { createIssue } from '@/lib/domain/issues';
import { getDashboard } from '@/lib/domain/dashboard';

let O: TestOrg;
let shiftId: string;

beforeAll(async () => {
  await resetDb();
  O = await makeOrg('Flow Org');
  const start = DateTime.now().set({ hour: 18, minute: 0 }).toJSDate();
  const shift = await createShift(O.orgId, {
    siteId: O.siteId,
    assignedUserId: O.cleanerId,
    scheduledStart: start,
    scheduledEnd: new Date(start.getTime() + 2 * 60 * 60 * 1000),
  });
  shiftId = shift.id;
});

describe('crew shift flow → owner verification (core loop)', () => {
  it('shows the shift in the crew today list', async () => {
    const today = await getCrewToday(O.orgId, O.cleanerId);
    expect(today.map((t) => t.shift.id)).toContain(shiftId);
  });

  it('clock-in inside the geofence flags within=true and snapshots the checklist', async () => {
    // Site is at 41.8781,-87.6298 with 150m radius (factory). Clock in nearby.
    const res = await clockIn(O.orgId, shiftId, O.cleanerId, 41.8782, -87.6299);
    expect(res.withinGeofence).toBe(true);

    const detail = await getShiftDetail(O.orgId, shiftId);
    expect(detail?.shift.status).toBe('in_progress');
    expect(detail?.shift.clockInWithinGeofence).toBe(true);
    expect(detail?.checklistTotal).toBe(2); // factory seeds 2 checklist items
    expect(detail?.checklistPct).toBe(0);
  });

  it('rejects clock-in by a non-assigned user', async () => {
    await expect(clockIn(O.orgId, shiftId, O.ownerId, 41.8782, -87.6299)).rejects.toThrow();
  });

  it('completes checklist items and attaches a proof photo', async () => {
    const detail = await getShiftDetail(O.orgId, shiftId);
    const items = detail!.results;
    await setChecklistResult(O.orgId, shiftId, O.cleanerId, items[0].id, true);
    await addShiftPhoto(O.orgId, shiftId, O.cleanerId, {
      storageKey: `${O.orgId}/proof.jpg`,
      url: '/api/storage/x',
      caption: 'Trash emptied',
      resultId: items[0].id,
    });
    await setChecklistResult(O.orgId, shiftId, O.cleanerId, items[1].id, true);

    const after = await getShiftDetail(O.orgId, shiftId);
    expect(after?.checklistPct).toBe(100);
    expect(after?.photos.length).toBe(1);
    expect(after?.results.find((r) => r.id === items[0].id)?.photoId).toBe(after?.photos[0].id);
  });

  it('crew can report an issue tied to the shift', async () => {
    const issue = await createIssue(O.orgId, {
      siteId: O.siteId,
      shiftId,
      reportedByUserId: O.cleanerId,
      source: 'crew',
      severity: 'medium',
      description: 'Vacuum belt broke mid-shift.',
    });
    const detail = await getShiftDetail(O.orgId, shiftId);
    expect(detail?.issues.map((i) => i.id)).toContain(issue.id);
  });

  it('clock-out marks the shift completed', async () => {
    const out = await clockOut(O.orgId, shiftId, O.cleanerId);
    expect(out.status).toBe('completed');
  });

  it('owner dashboard reflects the completed shift + open issue', async () => {
    const dash = await getDashboard(O.orgId);
    expect(dash.today.completed).toBeGreaterThanOrEqual(1);
    expect(dash.openIssues).toBeGreaterThanOrEqual(1);
  });
});
