'use client';
import { useActionState } from 'react';
import { generateQuoteAction } from '@/app/actions/quotes';
import type { FormState } from '@/app/actions/clients';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';

const initial: FormState = {};
type ClientOpt = { id: string; name: string };

export function QuoteForm({ clients, aiEnabled }: { clients: ClientOpt[]; aiEnabled: boolean }) {
  const [state, action, pending] = useActionState(generateQuoteAction, initial);
  return (
    <form action={action} className="space-y-4">
      {!aiEnabled && (
        <div className="rounded-md bg-[var(--color-warning)]/15 p-3 text-sm">
          AI quote generation needs <code>ANTHROPIC_API_KEY</code>. Add it to your environment
          (see README) to enable this feature.
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="clientId">Existing client (optional)</Label>
          <Select id="clientId" name="clientId" defaultValue="">
            <option value="">— Prospect (not yet a client) —</option>
            {clients.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="prospectName">Prospect name (if no client)</Label>
          <Input id="prospectName" name="prospectName" placeholder="Downtown Dental" />
        </div>
        <div className="space-y-1">
          <Label htmlFor="siteType">Site type</Label>
          <Select id="siteType" name="siteType" defaultValue="office">
            {['office', 'medical', 'retail', 'school', 'industrial', 'other'].map((t) => (
              <option key={t} value={t}>
                {t[0].toUpperCase() + t.slice(1)}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="frequency">Frequency</Label>
          <Select id="frequency" name="frequency" defaultValue="weekly">
            {['daily', 'weekly', 'biweekly', 'monthly', 'custom'].map((t) => (
              <option key={t} value={t}>
                {t[0].toUpperCase() + t.slice(1)}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="squareFootage">Square footage</Label>
          <Input id="squareFootage" name="squareFootage" type="number" min="0" placeholder="6000" />
        </div>
        <div className="space-y-1">
          <Label htmlFor="region">Region (for labor cost)</Label>
          <Input id="region" name="region" placeholder="Chicago, IL" />
        </div>
      </div>
      <div className="space-y-1">
        <Label htmlFor="scopeNotes">Scope notes / pasted RFP</Label>
        <Textarea
          id="scopeNotes"
          name="scopeNotes"
          required
          rows={5}
          placeholder="2 restrooms, lobby, 4 operatories. Nightly trash, weekly deep clean, disinfect surfaces…"
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor="ownerPricingHints">Your pricing hints (optional)</Label>
        <Input id="ownerPricingHints" name="ownerPricingHints" placeholder="we charge ~$0.10/sqft for offices" />
      </div>
      {state.error && <p className="text-sm text-[var(--color-destructive)]">{state.error}</p>}
      <Button type="submit" disabled={pending || !aiEnabled}>
        {pending ? 'Generating quote…' : 'Generate quote with AI'}
      </Button>
    </form>
  );
}
