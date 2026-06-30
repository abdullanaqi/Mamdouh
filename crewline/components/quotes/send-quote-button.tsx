'use client';
import { useActionState, useState } from 'react';
import { Send } from 'lucide-react';
import { sendQuoteAction } from '@/app/actions/quotes';
import type { FormState } from '@/app/actions/clients';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

const initial: FormState = {};

export function SendQuoteButton({
  quoteId,
  defaultEmail,
  disabled,
}: {
  quoteId: string;
  defaultEmail: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [state, action, pending] = useActionState(sendQuoteAction, initial);

  if (state.ok) return <span className="text-sm text-[var(--color-success)]">Proposal sent ✓</span>;
  if (!open) {
    return (
      <Button size="sm" onClick={() => setOpen(true)} disabled={disabled}>
        <Send className="size-4" /> Send proposal
      </Button>
    );
  }
  return (
    <form action={action} className="flex flex-wrap items-center gap-2">
      <input type="hidden" name="quoteId" value={quoteId} />
      <Input name="to" type="email" defaultValue={defaultEmail} placeholder="client@email.com" className="h-9 w-56" />
      <Button type="submit" size="sm" disabled={pending}>
        {pending ? 'Sending…' : 'Send'}
      </Button>
      {state.error && <span className="text-sm text-[var(--color-destructive)]">{state.error}</span>}
    </form>
  );
}
