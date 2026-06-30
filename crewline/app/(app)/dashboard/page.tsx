import { DateTime } from 'luxon';
import Link from 'next/link';
import {
  CheckCircle2,
  Clock,
  XCircle,
  CalendarClock,
  Receipt,
  AlertCircle,
  Flag,
} from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { getDashboard } from '@/lib/domain/dashboard';
import { getOrgTimezone } from '@/lib/domain/org';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ShiftStatusBadge } from '@/components/shift-status-badge';
import { EmptyState } from '@/components/empty-state';
import { formatCents } from '@/lib/utils';

export default async function DashboardPage() {
  const auth = await requireOwner();
  const tz = await getOrgTimezone(auth.orgId);
  const data = await getDashboard(auth.orgId);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {DateTime.now().setZone(tz).toFormat('cccc, LLLL d')} · Is my business OK right now?
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric icon={<CalendarClock className="size-5" />} label="Scheduled today" value={data.today.scheduled} />
        <Metric icon={<Clock className="size-5" />} label="In progress" value={data.today.inProgress} />
        <Metric icon={<CheckCircle2 className="size-5 text-[var(--color-success)]" />} label="Completed" value={data.today.completed} />
        <Metric
          icon={<XCircle className="size-5 text-[var(--color-destructive)]" />}
          label="Missed today"
          value={data.today.missed}
          alert={data.today.missed > 0}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Metric
          icon={<Receipt className="size-5" />}
          label="Outstanding invoices"
          value={formatCents(data.outstandingInvoiceCents)}
          sub={`${data.outstandingInvoiceCount} unpaid`}
          href="/invoices"
        />
        <Metric
          icon={<AlertCircle className="size-5" />}
          label="Open issues"
          value={data.openIssues}
          alert={data.openIssues > 0}
          href="/messages"
        />
        <Metric
          icon={<Flag className="size-5" />}
          label="Quality flags"
          value={data.qualityFlags}
          alert={data.qualityFlags > 0}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Today&apos;s coverage</CardTitle>
        </CardHeader>
        <CardContent>
          {data.todayShifts.length === 0 ? (
            <EmptyState
              title="No shifts scheduled today"
              body="Head to the schedule to add coverage."
              action={<Link href="/schedule" className="text-[var(--color-primary)] hover:underline">Open schedule</Link>}
            />
          ) : (
            <ul className="divide-y divide-[var(--color-border)]">
              {data.todayShifts.map((s) => (
                <li key={s.id} className="flex items-center justify-between gap-2 py-3">
                  <div>
                    <Link href={`/shifts/${s.id}`} className="font-medium hover:underline">
                      {s.siteName}
                    </Link>
                    <div className="text-xs text-[var(--color-muted-foreground)]">
                      {DateTime.fromJSDate(s.scheduledStart, { zone: tz }).toFormat('h:mm a')} ·{' '}
                      {s.assigneeName ?? 'Unassigned'}
                    </div>
                  </div>
                  <ShiftStatusBadge status={s.status} />
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Metric({
  icon,
  label,
  value,
  sub,
  href,
  alert,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  sub?: string;
  href?: string;
  alert?: boolean;
}) {
  const inner = (
    <Card className={alert ? 'border-[var(--color-warning)]' : undefined}>
      <CardContent className="flex items-center gap-3 pt-6">
        <div className="flex size-10 items-center justify-center rounded-lg bg-[var(--color-muted)]">
          {icon}
        </div>
        <div>
          <div className="text-xs text-[var(--color-muted-foreground)]">{label}</div>
          <div className="text-xl font-bold">{value}</div>
          {sub && <div className="text-xs text-[var(--color-muted-foreground)]">{sub}</div>}
        </div>
      </CardContent>
    </Card>
  );
  return href ? <Link href={href}>{inner}</Link> : inner;
}
