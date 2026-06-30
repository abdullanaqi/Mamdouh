import Link from 'next/link';
import { DateTime } from 'luxon';
import { MapPin, ChevronRight } from 'lucide-react';
import { requireCrew } from '@/lib/auth/context';
import { getCrewToday } from '@/lib/domain/shifts';
import { getOrgTimezone } from '@/lib/domain/org';
import { ShiftStatusBadge } from '@/components/shift-status-badge';
import { EmptyState } from '@/components/empty-state';

export const dynamic = 'force-dynamic';

export default async function TodayPage() {
  const auth = await requireCrew();
  const tz = await getOrgTimezone(auth.orgId);
  const shifts = await getCrewToday(auth.orgId, auth.userId);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Today</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {DateTime.now().setZone(tz).toFormat('cccc, LLL d')}
        </p>
      </div>

      {shifts.length === 0 ? (
        <EmptyState title="No shifts today" body="Enjoy your day off, or check back later." />
      ) : (
        <ul className="space-y-3">
          {shifts.map(({ shift, siteName, address }) => (
            <li key={shift.id}>
              <Link
                href={`/shift/${shift.id}`}
                className="crew-tap flex items-center justify-between gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 active:bg-[var(--color-muted)]"
              >
                <div className="min-w-0">
                  <div className="font-semibold">{siteName}</div>
                  <div className="mt-0.5 text-sm text-[var(--color-muted-foreground)]">
                    {DateTime.fromJSDate(shift.scheduledStart, { zone: tz }).toFormat('h:mm a')}
                    {' – '}
                    {DateTime.fromJSDate(shift.scheduledEnd, { zone: tz }).toFormat('h:mm a')}
                  </div>
                  {address && (
                    <div className="mt-1 flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
                      <MapPin className="size-3" /> {address}
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <ShiftStatusBadge status={shift.status} />
                  <ChevronRight className="size-5 text-[var(--color-muted-foreground)]" />
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
