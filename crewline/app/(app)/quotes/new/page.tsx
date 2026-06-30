import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { listClients } from '@/lib/domain/clients';
import { aiEnabled } from '@/lib/ai/enabled';
import { QuoteForm } from '@/components/forms/quote-form';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default async function NewQuotePage() {
  const auth = await requireOwner();
  const clients = await listClients(auth.orgId);
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <Link href="/quotes" className="flex items-center gap-1 text-sm text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
        <ArrowLeft className="size-4" /> Back to quotes
      </Link>
      <Card>
        <CardHeader>
          <CardTitle>New AI quote</CardTitle>
        </CardHeader>
        <CardContent>
          <QuoteForm
            clients={clients.map((c) => ({ id: c.id, name: c.name }))}
            aiEnabled={aiEnabled()}
          />
        </CardContent>
      </Card>
    </div>
  );
}
