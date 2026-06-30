'use client';
import { useRef } from 'react';
import { assignShiftAction } from '@/app/actions/schedule';
import { Select } from '@/components/ui/select';

type Crew = { id: string; fullName: string | null };

/** Inline reassignment: changing the select submits the assign server action. */
export function AssignSelect({
  shiftId,
  crew,
  current,
}: {
  shiftId: string;
  crew: Crew[];
  current: string | null;
}) {
  const formRef = useRef<HTMLFormElement>(null);
  return (
    <form ref={formRef} action={assignShiftAction}>
      <input type="hidden" name="shiftId" value={shiftId} />
      <Select
        name="assignedUserId"
        defaultValue={current ?? ''}
        className="h-8 text-xs"
        onChange={() => formRef.current?.requestSubmit()}
      >
        <option value="">Unassigned</option>
        {crew.map((c) => (
          <option key={c.id} value={c.id}>
            {c.fullName ?? 'Crew'}
          </option>
        ))}
      </Select>
    </form>
  );
}
