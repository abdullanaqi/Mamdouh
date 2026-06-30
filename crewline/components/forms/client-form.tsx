'use client';
import { useActionState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { createClientAction, updateClientAction, type FormState } from '@/app/actions/clients';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import type { Client } from '@/lib/db/schema';

const initial: FormState = {};

export function ClientForm({ client }: { client?: Client }) {
  const editing = Boolean(client);
  const [state, action, pending] = useActionState(
    editing ? updateClientAction : createClientAction,
    initial,
  );
  const router = useRouter();

  useEffect(() => {
    if (state.ok && state.id) {
      router.push(`/clients/${state.id}`);
    }
  }, [state, router]);

  return (
    <form action={action} className="space-y-4">
      {editing && <input type="hidden" name="clientId" value={client!.id} />}
      <div className="space-y-2">
        <Label htmlFor="name">Client name</Label>
        <Input id="name" name="name" required defaultValue={client?.name} placeholder="Acme Dental Group" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="contactName">Contact name</Label>
          <Input id="contactName" name="contactName" defaultValue={client?.contactName ?? ''} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="billingTerms">Billing terms</Label>
          <Select id="billingTerms" name="billingTerms" defaultValue={client?.billingTerms ?? 'net30'}>
            <option value="due_on_receipt">Due on receipt</option>
            <option value="net15">Net 15</option>
            <option value="net30">Net 30</option>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="contactEmail">Contact email</Label>
          <Input id="contactEmail" name="contactEmail" type="email" defaultValue={client?.contactEmail ?? ''} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="contactPhone">Contact phone</Label>
          <Input id="contactPhone" name="contactPhone" defaultValue={client?.contactPhone ?? ''} />
        </div>
      </div>
      <div className="space-y-2">
        <Label htmlFor="notes">Notes</Label>
        <Textarea id="notes" name="notes" defaultValue={client?.notes ?? ''} />
      </div>
      {state.error && <p className="text-sm text-[var(--color-destructive)]">{state.error}</p>}
      <div className="flex gap-2">
        <Button type="submit" disabled={pending}>
          {pending ? 'Saving…' : editing ? 'Save changes' : 'Create client'}
        </Button>
        <Button type="button" variant="outline" onClick={() => router.back()}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
