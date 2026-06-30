import { DateTime } from 'luxon';
import { AlertTriangle, CalendarPlus } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { listShiftsInRange, listAssignableCrew } from '@/lib/domain/scheduling';
import { listSites } from '@/lib/domain/sites';
import { getOrgTimezone } from '@/lib/domain/org';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ShiftStatusBadge } from '@/components/shift-status-badge';
import { AssignSelect } from '@/components/schedule/assign-select';
import { NewShiftForm } from '@/components/schedule/new-shift-form';
import { EmptyState } from '@/components/empty-state';

export default async function SchedulePage() {
  const auth = await requireOwner();
  const tz = await getOrgTimezone(auth.orgId);

  const from = DateTime.now().setZone(tz).startOf('day').toJSDate();
  const to = DateTime.now().setZone(tz).plus({ days: 13 }).endOf('day').toJSDate();

  const [rows, sites, crew] = await Promise.all([
    listShiftsInRange(auth.orgId, from, to),
    listSites(auth.orgId),
    listAssignableCrew(auth.orgId),
  ]);

  // Group by local day.
  const byDay = new Map<string, typeof rows>();
  for (const r of rows) {
    const key = DateTime.fromJSDate(r.shift.scheduledStart, { zone: tz }).toISODate()!;
    if (!byDay.has(key)) byDay.set(key, []);
    byDay.get(key)!.push(r);
  }
  const days = [...byDay.keys()].sort();
  const uncovered = rows.filter((r) => !r.shift.assignedUserId && r.shift.status === 'scheduled').length;

  const siteOpts = sites.map((s) => ({ id: s.site.id, name: s.site.name }));
  const crewOpts = crew.map((c) => ({ id: c.id, fullName: c.fullName }));

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Schedule</h1>
        {uncovered > 0 && (
          <Badge variant="warning" className="gap-1">
            <AlertTriangle className="size-3" /> {uncovered} uncovered
          </Badge>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          {days.length === 0 ? (
            <Card>
              <CardContent className="pt-6">
                <EmptyState
                  icon={<CalendarPlus className="size-8" />}
                  title="No shifts in the next two weeks"
                  body="Create a one-off shift or a recurring rule to populate the schedule."
                />
              </CardContent>
            </Card>
          ) : (
            days.map((day) => {
              const dayRows = byDay.get(day)!;
              const label = DateTime.fromISO(day, { zone: tz }).toFormat('cccc, LLL d');
              return (
                <Card key={day}>
                  <CardHeader className="py-3">
                    <CardTitle className="text-sm">{label}</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {dayRows.map((r) => (
                      <div
                        key={r.shift.id}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-[var(--color-border)] p-2"
                      >
                        <div className="min-w-0">
                          <a href={`/shifts/${r.shift.id}`} className="font-medium hover:underline">
                            {r.siteName}
                          </a>
                          <div className="text-xs text-[var(--color-muted-foreground)]">
                            {DateTime.fromJSDate(r.shift.scheduledStart, { zone: tz }).toFormat('h:mm a')}
                            {' – '}
                            {DateTime.fromJSDate(r.shift.scheduledEnd, { zone: tz }).toFormat('h:mm a')}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <ShiftStatusBadge status={r.shift.status} />
                          <AssignSelect shiftId={r.shift.id} crew={crewOpts} current={r.shift.assignedUserId} />
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              );
            })
          )}
        </div>

        <div>
          <Card className="sticky top-6">
            <CardHeader>
              <CardTitle>Add to schedule</CardTitle>
            </CardHeader>
            <CardContent>
              <NewShiftForm sites={siteOpts} crew={crewOpts} />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
