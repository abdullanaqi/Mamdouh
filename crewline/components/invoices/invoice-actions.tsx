'use client';
import { useActionState, useState } from 'react';
import { Send } from 'lucide-react';
import { sendInvoiceAction, markInvoicePaidAction } from '@/app/actions/invoices';
import type { FormState } from '@/app/actions/clients';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

const initial: FormState = {};

export function InvoiceActions({
  invoiceId,
  status,
  defaultEmail,
}: {
  invoiceId: string;
  status: string;
  defaultEmail: string;
}) {
  const [open, setOpen] = useState(false);
  const [state, sendAction, pending] = useActionState(sendInvoiceAction, initial);
  const canSend = status === 'draft' || status === 'sent' || status === 'overdue';
  const canPay = status !== 'paid' && status !== 'void';

  return (
    <div className="flex flex-wrap items-center gap-2">
      {state.ok ? (
        <span className="text-sm text-[var(--color-success)]">Invoice sent ✓</span>
      ) : !open ? (
        <Button size="sm" onClick={() => setOpen(true)} disabled={!canSend}>
          <Send className="size-4" /> Send invoice
        </Button>
      ) : (
        <form action={sendAction} className="flex flex-wrap items-center gap-2">
          <input type="hidden" name="invoiceId" value={invoiceId} />
          <Input name="to" type="email" defaultValue={defaultEmail} placeholder="client@email.com" className="h-9 w-56" />
          <Button type="submit" size="sm" disabled={pending}>
            {pending ? 'Sending…' : 'Send'}
          </Button>
          {state.error && <span className="text-sm text-[var(--color-destructive)]">{state.error}</span>}
        </form>
      )}

      {canPay && (
        <form action={markInvoicePaidAction}>
          <input type="hidden" name="invoiceId" value={invoiceId} />
          <Button type="submit" size="sm" variant="outline">
            Mark paid
          </Button>
        </form>
      )}
    </div>
  );
}
