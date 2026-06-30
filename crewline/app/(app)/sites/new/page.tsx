import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { getClient, listClients } from '@/lib/domain/clients';
import { SiteForm } from '@/components/forms/site-form';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default async function NewSitePage({
  searchParams,
}: {
  searchParams: Promise<{ clientId?: string }>;
}) {
  const auth = await requireOwner();
  const { clientId } = await searchParams;

  // A site must belong to a client; if none chosen, send the owner to pick one.
  if (!clientId) {
    const clients = await listClients(auth.orgId);
    if (clients.length === 0) redirect('/clients/new');
    redirect(`/sites/new?clientId=${clients[0].id}`);
  }
  const client = await getClient(auth.orgId, clientId);
  if (!client) notFound();

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <Link href={`/clients/${client.id}`} className="flex items-center gap-1 text-sm text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
        <ArrowLeft className="size-4" /> Back to {client.name}
      </Link>
      <Card>
        <CardHeader>
          <CardTitle>New site for {client.name}</CardTitle>
        </CardHeader>
        <CardContent>
          <SiteForm clientId={client.id} />
        </CardContent>
      </Card>
    </div>
  );
}
