import Link from 'next/link';
import { DateTime } from 'luxon';
import { requireCrew } from '@/lib/auth/context';
import { getCrewHistory } from '@/lib/domain/shifts';
import { getOrgTimezone } from '@/lib/domain/org';
import { ShiftStatusBadge } from '@/components/shift-status-badge';
import { EmptyState } from '@/components/empty-state';

export const dynamic = 'force-dynamic';

export default async function HistoryPage() {
  const auth = await requireCrew();
  const tz = await getOrgTimezone(auth.orgId);
  const rows = await getCrewHistory(auth.orgId, auth.userId);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">History</h1>
      {rows.length === 0 ? (
        <EmptyState title="No shifts yet" body="Your completed shifts will show up here." />
      ) : (
        <ul className="space-y-2">
          {rows.map(({ shift, siteName }) => (
            <li key={shift.id}>
              <Link
                href={`/shift/${shift.id}`}
                className="flex items-center justify-between rounded-lg border border-[var(--color-border)] p-3"
              >
                <div>
                  <div className="font-medium">{siteName}</div>
                  <div className="text-xs text-[var(--color-muted-foreground)]">
                    {DateTime.fromJSDate(shift.scheduledStart, { zone: tz }).toFormat('LLL d, h:mm a')}
                  </div>
                </div>
                <ShiftStatusBadge status={shift.status} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
