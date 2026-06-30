'use client';
import { useActionState, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Plus, Trash2 } from 'lucide-react';
import { createSiteAction, updateSiteAction, type FormState } from '@/app/actions/clients';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import type { Site, SiteChecklistItem } from '@/lib/db/schema';

const initial: FormState = {};

type Row = { label: string; area: string; requiresPhoto: boolean };

export function SiteForm({
  clientId,
  site,
  checklist,
}: {
  clientId: string;
  site?: Site;
  checklist?: SiteChecklistItem[];
}) {
  const editing = Boolean(site);
  const [state, action, pending] = useActionState(
    editing ? updateSiteAction : createSiteAction,
    initial,
  );
  const router = useRouter();
  const [rows, setRows] = useState<Row[]>(
    checklist?.length
      ? checklist.map((c) => ({ label: c.label, area: c.area ?? '', requiresPhoto: c.requiresPhoto }))
      : [{ label: '', area: '', requiresPhoto: false }],
  );

  useEffect(() => {
    if (state.ok && state.id) router.push(`/sites/${state.id}`);
  }, [state, router]);

  const updateRow = (i: number, patch: Partial<Row>) =>
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));

  return (
    <form action={action} className="space-y-6">
      <input type="hidden" name="clientId" value={clientId} />
      {editing && <input type="hidden" name="siteId" value={site!.id} />}

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2 sm:col-span-2">
          <Label htmlFor="name">Site name</Label>
          <Input id="name" name="name" required defaultValue={site?.name} placeholder="Acme Dental — North Office" />
        </div>
        <div className="space-y-2 sm:col-span-2">
          <Label htmlFor="address">Address</Label>
          <Input id="address" name="address" defaultValue={site?.address ?? ''} placeholder="233 S Wacker Dr, Chicago, IL" />
        </div>
        <div className="space-y-2">
          <Label htmlFor="siteType">Site type</Label>
          <Select id="siteType" name="siteType" defaultValue={site?.siteType ?? 'office'}>
            {['office', 'medical', 'retail', 'school', 'industrial', 'other'].map((t) => (
              <option key={t} value={t}>
                {t[0].toUpperCase() + t.slice(1)}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="serviceFrequency">Service frequency</Label>
          <Select id="serviceFrequency" name="serviceFrequency" defaultValue={site?.serviceFrequency ?? 'weekly'}>
            {['daily', 'weekly', 'biweekly', 'monthly', 'custom'].map((t) => (
              <option key={t} value={t}>
                {t[0].toUpperCase() + t.slice(1)}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="squareFootage">Square footage</Label>
          <Input id="squareFootage" name="squareFootage" type="number" min="0" defaultValue={site?.squareFootage ?? ''} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="contractRateDollars">Contract rate (USD / service)</Label>
          <Input
            id="contractRateDollars"
            name="contractRateDollars"
            type="number"
            min="0"
            step="0.01"
            defaultValue={site?.contractRateCents != null ? (site.contractRateCents / 100).toFixed(2) : ''}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="lat">Latitude (geofence)</Label>
          <Input id="lat" name="lat" type="number" step="any" defaultValue={site?.lat ?? ''} placeholder="41.8789" />
        </div>
        <div className="space-y-2">
          <Label htmlFor="lng">Longitude (geofence)</Label>
          <Input id="lng" name="lng" type="number" step="any" defaultValue={site?.lng ?? ''} placeholder="-87.6359" />
        </div>
        <div className="space-y-2">
          <Label htmlFor="geofenceRadiusM">Geofence radius (m)</Label>
          <Input id="geofenceRadiusM" name="geofenceRadiusM" type="number" min="10" defaultValue={site?.geofenceRadiusM ?? 150} />
        </div>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <Label>Checklist</Label>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setRows((rs) => [...rs, { label: '', area: '', requiresPhoto: false }])}
          >
            <Plus className="size-4" /> Add task
          </Button>
        </div>
        <div className="space-y-2">
          {rows.map((row, i) => (
            <div key={i} className="flex items-center gap-2 rounded-md border border-[var(--color-border)] p-2">
              <Input
                name="checklist_label"
                placeholder="Empty all trash bins"
                value={row.label}
                onChange={(e) => updateRow(i, { label: e.target.value })}
                className="flex-1"
              />
              <Input
                name="checklist_area"
                placeholder="Area"
                value={row.area}
                onChange={(e) => updateRow(i, { area: e.target.value })}
                className="w-32"
              />
              <label className="flex items-center gap-1 whitespace-nowrap text-xs">
                <input
                  type="checkbox"
                  checked={row.requiresPhoto}
                  onChange={(e) => updateRow(i, { requiresPhoto: e.target.checked })}
                />
                Photo
                <input type="hidden" name="checklist_photo" value={String(row.requiresPhoto)} />
              </label>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => setRows((rs) => rs.filter((_, idx) => idx !== i))}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          ))}
        </div>
      </div>

      {state.error && <p className="text-sm text-[var(--color-destructive)]">{state.error}</p>}
      <div className="flex gap-2">
        <Button type="submit" disabled={pending}>
          {pending ? 'Saving…' : editing ? 'Save site' : 'Create site'}
        </Button>
        <Button type="button" variant="outline" onClick={() => router.back()}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
