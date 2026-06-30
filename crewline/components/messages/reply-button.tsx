'use client';
import { useActionState } from 'react';
import { Sparkles } from 'lucide-react';
import { draftReplyAction, type CommsState } from '@/app/actions/comms';
import { Button } from '@/components/ui/button';

const initial: CommsState = {};

export function MessageReplyButton({ messageId, aiEnabled }: { messageId: string; aiEnabled: boolean }) {
  const [state, action, pending] = useActionState(draftReplyAction, initial);
  return (
    <form action={action} className="space-y-1">
      <input type="hidden" name="messageId" value={messageId} />
      <Button type="submit" size="sm" variant="outline" disabled={pending || !aiEnabled}>
        <Sparkles className="size-4" /> {pending ? 'Drafting…' : 'Draft AI reply'}
      </Button>
      {!aiEnabled && (
        <p className="text-xs text-[var(--color-muted-foreground)]">
          Add ANTHROPIC_API_KEY to enable AI replies.
        </p>
      )}
      {state.error && <p className="text-xs text-[var(--color-destructive)]">{state.error}</p>}
      {state.ok && <p className="text-xs text-[var(--color-success)]">Draft added below.</p>}
    </form>
  );
}
