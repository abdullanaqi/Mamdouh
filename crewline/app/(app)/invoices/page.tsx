import Link from 'next/link';
import { Receipt, Plus } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { listInvoices } from '@/lib/domain/invoices';
import { DateTime } from 'luxon';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { EmptyState } from '@/components/empty-state';
import { formatCents } from '@/lib/utils';

export default async function InvoicesPage() {
  const auth = await requireOwner();
  const invoices = await listInvoices(auth.orgId);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Invoices</h1>
        <Button asChild>
          <Link href="/invoices/new">
            <Plus className="size-4" /> New invoice
          </Link>
        </Button>
      </div>
      <Card>
        <CardContent className="pt-6">
          {invoices.length === 0 ? (
            <EmptyState
              icon={<Receipt className="size-8" />}
              title="No invoices yet"
              body="Generate an invoice from a site's completed shifts and send it to your client."
              action={<Link href="/invoices/new" className="text-[var(--color-primary)] hover:underline">Create an invoice</Link>}
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Number</TableHead>
                  <TableHead>Client</TableHead>
                  <TableHead>Total</TableHead>
                  <TableHead>Due</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {invoices.map((i) => (
                  <TableRow key={i.id}>
                    <TableCell>
                      <Link href={`/invoices/${i.id}`} className="font-medium hover:underline">
                        {i.number}
                      </Link>
                    </TableCell>
                    <TableCell className="text-[var(--color-muted-foreground)]">{i.clientName}</TableCell>
                    <TableCell>{formatCents(i.totalCents)}</TableCell>
                    <TableCell>
                      {i.dueDate ? DateTime.fromISO(i.dueDate).toFormat('LLL d') : '—'}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          i.status === 'paid' ? 'success' : i.status === 'overdue' ? 'destructive' : 'secondary'
                        }
                      >
                        {i.status}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
