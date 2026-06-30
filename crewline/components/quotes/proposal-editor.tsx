'use client';
import { useActionState } from 'react';
import { saveProposalAction } from '@/app/actions/quotes';
import type { FormState } from '@/app/actions/clients';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';

const initial: FormState = {};

export function ProposalEditor({ quoteId, proposalText }: { quoteId: string; proposalText: string }) {
  const [state, action, pending] = useActionState(saveProposalAction, initial);
  return (
    <form action={action} className="space-y-2">
      <input type="hidden" name="quoteId" value={quoteId} />
      <Textarea name="proposalText" defaultValue={proposalText} rows={12} />
      <div className="flex items-center gap-2">
        <Button type="submit" disabled={pending}>
          {pending ? 'Saving…' : 'Save proposal'}
        </Button>
        {state.ok && <span className="text-sm text-[var(--color-success)]">Saved.</span>}
      </div>
    </form>
  );
}
