import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { getInvoice } from '@/lib/domain/invoices';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { InvoiceActions } from '@/components/invoices/invoice-actions';
import { formatCents } from '@/lib/utils';

export default async function InvoiceDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const auth = await requireOwner();
  const { id } = await params;
  const inv = await getInvoice(auth.orgId, id);
  if (!inv) notFound();

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Link href="/invoices" className="flex items-center gap-1 text-sm text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
        <ArrowLeft className="size-4" /> Back to invoices
      </Link>

      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">{inv.number}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {inv.clientName}
            {inv.periodStart && inv.periodEnd ? ` · ${inv.periodStart} → ${inv.periodEnd}` : ''}
          </p>
        </div>
        <Badge variant={inv.status === 'paid' ? 'success' : inv.status === 'overdue' ? 'destructive' : 'secondary'}>
          {inv.status}
        </Badge>
      </div>

      <Card>
        <CardContent className="pt-6">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Description</TableHead>
                <TableHead>Qty</TableHead>
                <TableHead>Unit</TableHead>
                <TableHead>Amount</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {inv.lineItems.map((l) => (
                <TableRow key={l.id}>
                  <TableCell>{l.description}</TableCell>
                  <TableCell>{Number(l.quantity)}</TableCell>
                  <TableCell>{formatCents(l.unitPriceCents)}</TableCell>
                  <TableCell>{formatCents(l.amountCents)}</TableCell>
                </TableRow>
              ))}
              <TableRow>
                <TableCell colSpan={3} className="text-right text-[var(--color-muted-foreground)]">
                  Subtotal
                </TableCell>
                <TableCell>{formatCents(inv.subtotalCents)}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell colSpan={3} className="text-right text-[var(--color-muted-foreground)]">
                  Tax
                </TableCell>
                <TableCell>{formatCents(inv.taxCents)}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell colSpan={3} className="text-right font-bold">
                  Total
                </TableCell>
                <TableCell className="font-bold">{formatCents(inv.totalCents)}</TableCell>
              </TableRow>
            </TableBody>
          </Table>
          {inv.dueDate && (
            <p className="mt-3 text-sm text-[var(--color-muted-foreground)]">Due by {inv.dueDate}</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Actions</CardTitle>
        </CardHeader>
        <CardContent>
          <InvoiceActions invoiceId={inv.id} status={inv.status} defaultEmail={inv.clientEmail ?? ''} />
        </CardContent>
      </Card>
    </div>
  );
}
