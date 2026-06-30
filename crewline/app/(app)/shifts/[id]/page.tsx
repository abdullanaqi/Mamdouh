import { notFound } from 'next/navigation';
import { DateTime } from 'luxon';
import { MapPin, MapPinOff, CheckCircle2, Circle, Camera, AlertTriangle } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { getShiftDetail } from '@/lib/domain/shifts';
import { getOrgTimezone } from '@/lib/domain/org';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ShiftStatusBadge } from '@/components/shift-status-badge';

function fmt(d: Date | null, tz: string) {
  return d ? DateTime.fromJSDate(d, { zone: tz }).toFormat('LLL d, h:mm a') : '—';
}

export default async function ShiftVerificationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const auth = await requireOwner();
  const { id } = await params;
  const detail = await getShiftDetail(auth.orgId, id);
  if (!detail) notFound();
  const tz = await getOrgTimezone(auth.orgId);
  const s = detail.shift;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">{detail.site.name}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {fmt(s.scheduledStart, tz)} · {detail.assigneeName ?? 'Unassigned'}
          </p>
        </div>
        <ShiftStatusBadge status={s.status} />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Clock in" value={fmt(s.clockInAt, tz)} />
        <Stat label="Clock out" value={fmt(s.clockOutAt, tz)} />
        <Stat
          label="Geofence"
          value={
            s.clockInWithinGeofence == null ? (
              '—'
            ) : s.clockInWithinGeofence ? (
              <span className="flex items-center gap-1 text-[var(--color-success)]">
                <MapPin className="size-4" /> Verified on-site
              </span>
            ) : (
              <span className="flex items-center gap-1 text-[var(--color-warning)]">
                <MapPinOff className="size-4" /> Outside geofence
              </span>
            )
          }
        />
      </div>

      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>Checklist</CardTitle>
          <Badge variant={detail.checklistPct === 100 ? 'success' : 'secondary'}>
            {detail.checklistDone}/{detail.checklistTotal} · {detail.checklistPct}%
          </Badge>
        </CardHeader>
        <CardContent>
          {detail.results.length === 0 ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">No checklist recorded.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {detail.results.map((r) => (
                <li key={r.id} className="flex items-center gap-2">
                  {r.completed ? (
                    <CheckCircle2 className="size-4 text-[var(--color-success)]" />
                  ) : (
                    <Circle className="size-4 text-[var(--color-muted-foreground)]" />
                  )}
                  <span className={r.completed ? '' : 'text-[var(--color-muted-foreground)]'}>
                    {r.labelSnapshot}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Proof photos</CardTitle>
        </CardHeader>
        <CardContent>
          {detail.photos.length === 0 ? (
            <p className="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
              <Camera className="size-4" /> No photos uploaded.
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {detail.photos.map((p) => (
                <figure key={p.id} className="overflow-hidden rounded-lg border border-[var(--color-border)]">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={p.url} alt={p.caption ?? 'Proof photo'} className="aspect-square w-full object-cover" />
                  <figcaption className="space-y-1 p-2 text-xs">
                    <div className="truncate">{p.caption ?? 'Untitled'}</div>
                    {p.aiPass === false ? (
                      <Badge variant="warning" className="gap-1">
                        <AlertTriangle className="size-3" /> AI flagged
                      </Badge>
                    ) : p.aiPass === true ? (
                      <Badge variant="success">AI pass</Badge>
                    ) : (
                      <Badge variant="outline">Not reviewed</Badge>
                    )}
                  </figcaption>
                </figure>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {(s.aiQualityScore != null || s.aiQualityNotes) && (
        <Card>
          <CardHeader>
            <CardTitle>AI quality scan</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            {s.aiQualityScore != null && (
              <div>
                Score: <strong>{s.aiQualityScore}/100</strong>
              </div>
            )}
            {s.aiQualityNotes && <p className="text-[var(--color-muted-foreground)]">{s.aiQualityNotes}</p>}
            <p className="text-xs text-[var(--color-muted-foreground)]">
              AI checks are advisory — they flag for your review and never penalize a crew.
            </p>
          </CardContent>
        </Card>
      )}

      {detail.issues.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Issues</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              {detail.issues.map((i) => (
                <li key={i.id} className="flex items-center justify-between gap-2">
                  <span>{i.description}</span>
                  <Badge variant={i.severity === 'high' ? 'destructive' : 'secondary'}>{i.severity}</Badge>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3">
      <div className="text-xs text-[var(--color-muted-foreground)]">{label}</div>
      <div className="mt-1 text-sm font-medium">{value}</div>
    </div>
  );
}
