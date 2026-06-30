import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowLeft, Camera } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { getSiteWithChecklist } from '@/lib/domain/sites';
import { getClient } from '@/lib/domain/clients';
import { SiteForm } from '@/components/forms/site-form';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

export default async function SiteDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const auth = await requireOwner();
  const { id } = await params;
  const site = await getSiteWithChecklist(auth.orgId, id);
  if (!site) notFound();
  const client = await getClient(auth.orgId, site.clientId);

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Link href={`/clients/${site.clientId}`} className="flex items-center gap-1 text-sm text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
        <ArrowLeft className="size-4" /> Back to {client?.name ?? 'client'}
      </Link>

      <div>
        <h1 className="text-2xl font-bold">{site.name}</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">{site.address ?? 'No address'}</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Current checklist</CardTitle>
        </CardHeader>
        <CardContent>
          {site.checklist.length === 0 ? (
            <p className="text-sm text-[var(--color-muted-foreground)]">No checklist items yet.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {site.checklist.map((c) => (
                <li key={c.id} className="flex items-center gap-2">
                  <span className="font-medium">{c.label}</span>
                  {c.area && <Badge variant="outline">{c.area}</Badge>}
                  {c.requiresPhoto && (
                    <span className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
                      <Camera className="size-3" /> photo
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Edit site & checklist</CardTitle>
        </CardHeader>
        <CardContent>
          <SiteForm clientId={site.clientId} site={site} checklist={site.checklist} />
        </CardContent>
      </Card>
    </div>
  );
}
