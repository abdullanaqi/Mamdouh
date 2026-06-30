'use client';
import { useActionState } from 'react';
import { inviteMemberAction } from '@/app/actions/team';
import type { FormState } from '@/app/actions/clients';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';

const initial: FormState = {};

export function InviteForm() {
  const [state, action, pending] = useActionState(inviteMemberAction, initial);
  return (
    <form action={action} className="grid gap-3 sm:grid-cols-2">
      <div className="space-y-1">
        <Label htmlFor="fullName">Name</Label>
        <Input id="fullName" name="fullName" required />
      </div>
      <div className="space-y-1">
        <Label htmlFor="role">Role</Label>
        <Select id="role" name="role" defaultValue="cleaner">
          <option value="cleaner">Cleaner (crew)</option>
          <option value="admin">Admin</option>
        </Select>
      </div>
      <div className="space-y-1">
        <Label htmlFor="email">Email (admins)</Label>
        <Input id="email" name="email" type="email" />
      </div>
      <div className="space-y-1">
        <Label htmlFor="phone">Phone (crew, E.164)</Label>
        <Input id="phone" name="phone" placeholder="+15555550123" />
      </div>
      {state.error && <p className="text-sm text-[var(--color-destructive)] sm:col-span-2">{state.error}</p>}
      {state.ok && <p className="text-sm text-[var(--color-success)] sm:col-span-2">Member invited.</p>}
      <div className="sm:col-span-2">
        <Button type="submit" disabled={pending}>
          {pending ? 'Inviting…' : 'Invite member'}
        </Button>
      </div>
    </form>
  );
}
