import Link from 'next/link';
import { Building2, Plus } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { listClientsWithSiteCount } from '@/lib/domain/clients';
import { listSites } from '@/lib/domain/sites';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { EmptyState } from '@/components/empty-state';

export default async function ClientsPage() {
  const auth = await requireOwner();
  const [clients, sites] = await Promise.all([
    listClientsWithSiteCount(auth.orgId),
    listSites(auth.orgId),
  ]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Clients & Sites</h1>
        <Button asChild>
          <Link href="/clients/new">
            <Plus className="size-4" /> New client
          </Link>
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Clients</CardTitle>
        </CardHeader>
        <CardContent>
          {clients.length === 0 ? (
            <EmptyState
              icon={<Building2 className="size-8" />}
              title="No clients yet"
              body="Add your first client, then create the sites you clean for them."
              action={<Link href="/clients/new" className="text-[var(--color-primary)] hover:underline">Add a client</Link>}
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Contact</TableHead>
                  <TableHead>Billing</TableHead>
                  <TableHead>Sites</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {clients.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell>
                      <Link href={`/clients/${c.id}`} className="font-medium hover:underline">
                        {c.name}
                      </Link>
                    </TableCell>
                    <TableCell className="text-[var(--color-muted-foreground)]">
                      {c.contactName ?? '—'}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{c.billingTerms}</Badge>
                    </TableCell>
                    <TableCell>{c.siteCount}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {sites.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>All sites</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Site</TableHead>
                  <TableHead>Client</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Frequency</TableHead>
                  <TableHead>Geofence</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sites.map(({ site, clientName }) => (
                  <TableRow key={site.id}>
                    <TableCell>
                      <Link href={`/sites/${site.id}`} className="font-medium hover:underline">
                        {site.name}
                      </Link>
                    </TableCell>
                    <TableCell className="text-[var(--color-muted-foreground)]">{clientName}</TableCell>
                    <TableCell className="capitalize">{site.siteType}</TableCell>
                    <TableCell className="capitalize">{site.serviceFrequency}</TableCell>
                    <TableCell>
                      {site.lat != null && site.lng != null ? (
                        <Badge variant="success">set</Badge>
                      ) : (
                        <Badge variant="warning">none</Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
