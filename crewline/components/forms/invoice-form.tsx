'use client';
import { useActionState } from 'react';
import { DateTime } from 'luxon';
import { generateInvoiceAction } from '@/app/actions/invoices';
import type { FormState } from '@/app/actions/clients';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';

const initial: FormState = {};
type SiteOpt = { siteId: string; siteName: string; clientId: string; clientName: string };

export function InvoiceForm({ sites }: { sites: SiteOpt[] }) {
  const [state, action, pending] = useActionState(generateInvoiceAction, initial);
  const monthStart = DateTime.now().startOf('month').toISODate()!;
  const monthEnd = DateTime.now().endOf('month').toISODate()!;

  if (sites.length === 0) {
    return (
      <p className="text-sm text-[var(--color-muted-foreground)]">
        Add a client and an active site (with a contract rate) before invoicing.
      </p>
    );
  }

  return (
    <form action={action} className="space-y-4">
      <div className="space-y-1">
        <Label htmlFor="clientSite">Site to invoice</Label>
        <Select id="clientSite" name="clientSite" required>
          {sites.map((s) => (
            <option key={s.siteId} value={`${s.clientId}:${s.siteId}`}>
              {s.clientName} — {s.siteName}
            </option>
          ))}
        </Select>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label htmlFor="periodStart">Period start</Label>
          <Input id="periodStart" name="periodStart" type="date" defaultValue={monthStart} required />
        </div>
        <div className="space-y-1">
          <Label htmlFor="periodEnd">Period end</Label>
          <Input id="periodEnd" name="periodEnd" type="date" defaultValue={monthEnd} required />
        </div>
      </div>
      <div className="space-y-1">
        <Label htmlFor="taxDollars">Tax (USD)</Label>
        <Input id="taxDollars" name="taxDollars" type="number" min="0" step="0.01" defaultValue="0" />
      </div>
      <p className="text-xs text-[var(--color-muted-foreground)]">
        Line items are generated from completed shifts at the site&apos;s contract rate.
      </p>
      {state.error && <p className="text-sm text-[var(--color-destructive)]">{state.error}</p>}
      <Button type="submit" disabled={pending}>
        {pending ? 'Generating…' : 'Generate invoice'}
      </Button>
    </form>
  );
}
