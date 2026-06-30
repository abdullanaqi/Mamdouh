'use client';
import { useActionState, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  createOneOffShiftAction,
  createRecurrenceAction,
} from '@/app/actions/schedule';
import type { FormState } from '@/app/actions/clients';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';

const initial: FormState = {};
const DAYS = [
  { key: 'mon', label: 'Mon' },
  { key: 'tue', label: 'Tue' },
  { key: 'wed', label: 'Wed' },
  { key: 'thu', label: 'Thu' },
  { key: 'fri', label: 'Fri' },
  { key: 'sat', label: 'Sat' },
  { key: 'sun', label: 'Sun' },
];

type SiteOpt = { id: string; name: string };
type CrewOpt = { id: string; fullName: string | null };

export function NewShiftForm({ sites, crew }: { sites: SiteOpt[]; crew: CrewOpt[] }) {
  const [mode, setMode] = useState<'one_off' | 'recurring'>('one_off');
  const [oneOff, oneOffAction, oneOffPending] = useActionState(createOneOffShiftAction, initial);
  const [rec, recAction, recPending] = useActionState(createRecurrenceAction, initial);
  const router = useRouter();

  useEffect(() => {
    if (oneOff.ok || rec.ok) router.refresh();
  }, [oneOff.ok, rec.ok, router]);

  if (sites.length === 0) {
    return (
      <p className="text-sm text-[var(--color-muted-foreground)]">
        Add a site first to schedule shifts.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-1 rounded-lg bg-[var(--color-muted)] p-1">
        <button
          type="button"
          onClick={() => setMode('one_off')}
          className={`rounded-md py-1.5 text-sm font-medium ${mode === 'one_off' ? 'bg-white shadow-sm' : 'text-[var(--color-muted-foreground)]'}`}
        >
          One-off
        </button>
        <button
          type="button"
          onClick={() => setMode('recurring')}
          className={`rounded-md py-1.5 text-sm font-medium ${mode === 'recurring' ? 'bg-white shadow-sm' : 'text-[var(--color-muted-foreground)]'}`}
        >
          Recurring
        </button>
      </div>

      {mode === 'one_off' ? (
        <form action={oneOffAction} className="space-y-3" key="one_off">
          <SiteField sites={sites} />
          <div className="grid grid-cols-2 gap-3">
            <Field label="Date">
              <Input name="date" type="date" required />
            </Field>
            <Field label="Start time">
              <Input name="time" type="time" defaultValue="18:00" required />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Duration (min)">
              <Input name="durationMinutes" type="number" defaultValue={120} min={15} />
            </Field>
            <CrewField crew={crew} />
          </div>
          {oneOff.error && <p className="text-sm text-[var(--color-destructive)]">{oneOff.error}</p>}
          <Button type="submit" disabled={oneOffPending}>
            {oneOffPending ? 'Adding…' : 'Add shift'}
          </Button>
        </form>
      ) : (
        <form action={recAction} className="space-y-3" key="recurring">
          <SiteField sites={sites} />
          <Field label="Repeat on">
            <div className="flex flex-wrap gap-1">
              {DAYS.map((d) => (
                <label
                  key={d.key}
                  className="flex cursor-pointer items-center gap-1 rounded-md border border-[var(--color-border)] px-2 py-1 text-xs"
                >
                  <input type="checkbox" name="weekday" value={d.key} /> {d.label}
                </label>
              ))}
            </div>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Start time">
              <Input name="time" type="time" defaultValue="18:00" required />
            </Field>
            <Field label="Duration (min)">
              <Input name="durationMinutes" type="number" defaultValue={120} min={15} />
            </Field>
          </div>
          <CrewField crew={crew} />
          {rec.error && <p className="text-sm text-[var(--color-destructive)]">{rec.error}</p>}
          <Button type="submit" disabled={recPending}>
            {recPending ? 'Creating…' : 'Create recurring shifts'}
          </Button>
        </form>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function SiteField({ sites }: { sites: SiteOpt[] }) {
  return (
    <Field label="Site">
      <Select name="siteId" required>
        {sites.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </Select>
    </Field>
  );
}

function CrewField({ crew }: { crew: CrewOpt[] }) {
  return (
    <Field label="Assign to">
      <Select name="assignedUserId" defaultValue="">
        <option value="">Unassigned</option>
        {crew.map((c) => (
          <option key={c.id} value={c.id}>
            {c.fullName ?? 'Crew'}
          </option>
        ))}
      </Select>
    </Field>
  );
}
