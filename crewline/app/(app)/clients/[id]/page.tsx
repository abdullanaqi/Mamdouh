import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowLeft, Plus, MapPin } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { getClient } from '@/lib/domain/clients';
import { listSitesForClient } from '@/lib/domain/sites';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { EmptyState } from '@/components/empty-state';

export default async function ClientDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const auth = await requireOwner();
  const { id } = await params;
  const client = await getClient(auth.orgId, id);
  if (!client) notFound();
  const sites = await listSitesForClient(auth.orgId, id);

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Link href="/clients" className="flex items-center gap-1 text-sm text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
        <ArrowLeft className="size-4" /> Back to clients
      </Link>

      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">{client.name}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {client.contactName ?? 'No contact'} · {client.contactEmail ?? 'no email'} ·{' '}
            <Badge variant="outline">{client.billingTerms}</Badge>
          </p>
        </div>
        <Button asChild variant="outline">
          <Link href={`/clients/${client.id}/edit`}>Edit</Link>
        </Button>
      </div>

      {client.notes && (
        <Card>
          <CardContent className="pt-6 text-sm">{client.notes}</CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>Sites</CardTitle>
          <Button asChild size="sm">
            <Link href={`/sites/new?clientId=${client.id}`}>
              <Plus className="size-4" /> Add site
            </Link>
          </Button>
        </CardHeader>
        <CardContent>
          {sites.length === 0 ? (
            <EmptyState
              icon={<MapPin className="size-8" />}
              title="No sites yet"
              body="Add a site with its address, geofence, and cleaning checklist."
            />
          ) : (
            <ul className="divide-y divide-[var(--color-border)]">
              {sites.map((s) => (
                <li key={s.id} className="flex items-center justify-between py-3">
                  <div>
                    <Link href={`/sites/${s.id}`} className="font-medium hover:underline">
                      {s.name}
                    </Link>
                    <p className="text-xs text-[var(--color-muted-foreground)]">{s.address ?? 'No address'}</p>
                  </div>
                  <Badge variant="secondary" className="capitalize">
                    {s.serviceFrequency}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
