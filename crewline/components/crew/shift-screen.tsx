'use client';
import { useActionState, useState } from 'react';
import { MapPin, MapPinOff, CheckCircle2, Circle, Camera, AlertTriangle, LogIn, LogOut } from 'lucide-react';
import { useOffline } from '@/components/crew/offline-provider';
import { uploadPhotoAction, type CrewActionState } from '@/app/actions/crew';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Select } from '@/components/ui/select';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

type Result = { id: string; labelSnapshot: string; completed: boolean; photoId: string | null };
type Detail = {
  shiftId: string;
  siteId: string;
  siteName: string;
  address: string | null;
  status: string;
  startLabel: string;
  clockInWithinGeofence: boolean | null;
  results: Result[];
  requiresPhotoLabels: string[];
  hasPhotos: boolean;
};

export function CrewShiftScreen({ detail }: { detail: Detail }) {
  const { mutate, online } = useOffline();
  const [busy, setBusy] = useState(false);
  const [geoMsg, setGeoMsg] = useState<string | null>(null);
  const notStarted = detail.status === 'scheduled' || detail.status === 'missed';
  const inProgress = detail.status === 'in_progress';
  const completed = detail.status === 'completed';

  async function clockIn() {
    setBusy(true);
    setGeoMsg(null);
    try {
      const pos = await new Promise<GeolocationPosition>((resolve, reject) => {
        if (!('geolocation' in navigator)) return reject(new Error('No GPS'));
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 10000,
        });
      });
      const r = await mutate('/api/crew/clock-in', {
        shiftId: detail.shiftId,
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
      });
      if (r.queued) setGeoMsg('Saved offline — will sync when you have signal.');
      else if (!r.ok) setGeoMsg('Could not clock in. Try again.');
    } catch {
      setGeoMsg('Location is required to clock in. Enable GPS and try again.');
    } finally {
      setBusy(false);
    }
  }

  async function toggle(result: Result) {
    await mutate('/api/crew/checklist', {
      shiftId: detail.shiftId,
      resultId: result.id,
      completed: !result.completed,
    });
  }

  async function clockOut() {
    setBusy(true);
    await mutate('/api/crew/clock-out', { shiftId: detail.shiftId });
    setBusy(false);
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">{detail.siteName}</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">{detail.startLabel}</p>
        {detail.address && (
          <a
            className="mt-1 inline-flex items-center gap-1 text-sm text-[var(--color-primary)]"
            href={`https://maps.google.com/?q=${encodeURIComponent(detail.address)}`}
            target="_blank"
            rel="noreferrer"
          >
            <MapPin className="size-4" /> Open in Maps
          </a>
        )}
      </div>

      {/* Clock in / status */}
      {notStarted && (
        <Card>
          <CardContent className="space-y-3 pt-6">
            <Button onClick={clockIn} disabled={busy} size="xl" className="w-full">
              <LogIn className="size-5" /> {busy ? 'Getting location…' : 'Clock in'}
            </Button>
            {geoMsg && <p className="text-center text-sm text-[var(--color-warning)]">{geoMsg}</p>}
            {!online && (
              <p className="text-center text-xs text-[var(--color-muted-foreground)]">
                You&apos;re offline — your clock-in will be saved and synced automatically.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {(inProgress || completed) && detail.clockInWithinGeofence != null && (
        <div className="flex items-center gap-2 text-sm">
          {detail.clockInWithinGeofence ? (
            <Badge variant="success" className="gap-1">
              <MapPin className="size-3" /> On-site verified
            </Badge>
          ) : (
            <Badge variant="warning" className="gap-1">
              <MapPinOff className="size-3" /> Outside geofence
            </Badge>
          )}
        </div>
      )}

      {/* Checklist */}
      {(inProgress || completed) && (
        <Card>
          <CardContent className="pt-6">
            <h2 className="mb-2 font-semibold">Checklist</h2>
            {detail.results.length === 0 ? (
              <p className="text-sm text-[var(--color-muted-foreground)]">No tasks for this site.</p>
            ) : (
              <ul className="space-y-1">
                {detail.results.map((r) => (
                  <li key={r.id}>
                    <button
                      type="button"
                      onClick={() => toggle(r)}
                      disabled={completed}
                      className="crew-tap flex w-full items-center gap-3 rounded-md px-2 text-left active:bg-[var(--color-muted)] disabled:opacity-70"
                    >
                      {r.completed ? (
                        <CheckCircle2 className="size-6 shrink-0 text-[var(--color-success)]" />
                      ) : (
                        <Circle className="size-6 shrink-0 text-[var(--color-muted-foreground)]" />
                      )}
                      <span className={r.completed ? 'line-through opacity-70' : ''}>
                        {r.labelSnapshot}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}

      {/* Photo upload (online only) */}
      {(inProgress || completed) && (
        <PhotoUpload shiftId={detail.shiftId} online={online} results={detail.results} />
      )}

      {/* Report issue */}
      {!completed && <ReportIssue shiftId={detail.shiftId} siteId={detail.siteId} />}

      {/* Clock out */}
      {inProgress && (
        <Button onClick={clockOut} disabled={busy} size="xl" variant="default" className="w-full">
          <LogOut className="size-5" /> {busy ? 'Saving…' : 'Clock out'}
        </Button>
      )}

      {completed && (
        <p className="text-center text-sm font-medium text-[var(--color-success)]">
          Shift completed — nice work!
        </p>
      )}
    </div>
  );
}

function PhotoUpload({
  shiftId,
  online,
  results,
}: {
  shiftId: string;
  online: boolean;
  results: Result[];
}) {
  const [state, action, pending] = useActionState<CrewActionState, FormData>(uploadPhotoAction, {});
  return (
    <Card>
      <CardContent className="pt-6">
        <h2 className="mb-2 flex items-center gap-2 font-semibold">
          <Camera className="size-4" /> Add proof photo
        </h2>
        {!online ? (
          <p className="text-sm text-[var(--color-muted-foreground)]">
            Photos upload when you&apos;re back online.
          </p>
        ) : (
          <form action={action} className="space-y-2">
            <input type="hidden" name="shiftId" value={shiftId} />
            {results.length > 0 && (
              <Select name="resultId" defaultValue="">
                <option value="">General proof</option>
                {results.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.labelSnapshot}
                  </option>
                ))}
              </Select>
            )}
            <input
              type="text"
              name="caption"
              placeholder="Caption (optional)"
              className="flex h-10 w-full rounded-md border border-[var(--color-input)] bg-[var(--color-background)] px-3 py-2 text-sm"
            />
            <input
              type="file"
              name="photo"
              accept="image/*"
              capture="environment"
              required
              className="block w-full text-sm"
            />
            {state.error && <p className="text-sm text-[var(--color-destructive)]">{state.error}</p>}
            {state.ok && <p className="text-sm text-[var(--color-success)]">Photo uploaded.</p>}
            <Button type="submit" disabled={pending} className="w-full">
              {pending ? 'Uploading…' : 'Upload photo'}
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function ReportIssue({ shiftId, siteId }: { shiftId: string; siteId: string }) {
  const { mutate } = useOffline();
  const [open, setOpen] = useState(false);
  const [desc, setDesc] = useState('');
  const [severity, setSeverity] = useState('medium');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!open) {
    return (
      <Button variant="outline" className="w-full" onClick={() => setOpen(true)}>
        <AlertTriangle className="size-4" /> Report an issue
      </Button>
    );
  }

  async function submit() {
    if (!desc.trim()) return;
    setBusy(true);
    await mutate('/api/crew/issue', { shiftId, siteId, description: desc, severity });
    setBusy(false);
    setSent(true);
    setDesc('');
    setTimeout(() => {
      setSent(false);
      setOpen(false);
    }, 1500);
  }

  return (
    <Card>
      <CardContent className="space-y-2 pt-6">
        <h2 className="font-semibold">Report an issue</h2>
        <Textarea
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          placeholder="e.g. Door was locked / supplies out / spill needs attention"
        />
        <Select value={severity} onChange={(e) => setSeverity(e.target.value)}>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
        </Select>
        {sent && <p className="text-sm text-[var(--color-success)]">Issue reported.</p>}
        <div className="flex gap-2">
          <Button onClick={submit} disabled={busy || !desc.trim()} className="flex-1">
            {busy ? 'Sending…' : 'Send'}
          </Button>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
