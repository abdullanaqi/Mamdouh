import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { getQuote } from '@/lib/domain/quotes';
import { setQuoteStatusAction } from '@/app/actions/quotes';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { ProposalEditor } from '@/components/quotes/proposal-editor';
import { formatCents } from '@/lib/utils';
import type { QuoteLineItem } from '@/lib/domain/quotes';

export default async function QuoteDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const auth = await requireOwner();
  const { id } = await params;
  const quote = await getQuote(auth.orgId, id);
  if (!quote) notFound();
  const lineItems = (quote.lineItemsJson as QuoteLineItem[] | null) ?? [];

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Link href="/quotes" className="flex items-center gap-1 text-sm text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
        <ArrowLeft className="size-4" /> Back to quotes
      </Link>

      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">{quote.prospectName ?? 'Quote'}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)] capitalize">
            {quote.frequency} · {formatCents(quote.totalCents)}
          </p>
        </div>
        <Badge variant={quote.status === 'won' ? 'success' : quote.status === 'lost' ? 'destructive' : 'secondary'}>
          {quote.status}
        </Badge>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Itemized quote</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Description</TableHead>
                <TableHead>Basis</TableHead>
                <TableHead>Qty</TableHead>
                <TableHead>Amount</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {lineItems.map((li, i) => (
                <TableRow key={i}>
                  <TableCell>
                    {li.description}
                    {li.area ? <span className="text-[var(--color-muted-foreground)]"> · {li.area}</span> : null}
                  </TableCell>
                  <TableCell className="text-[var(--color-muted-foreground)]">{li.unitBasis}</TableCell>
                  <TableCell>{li.quantity}</TableCell>
                  <TableCell>{formatCents(li.amountCents)}</TableCell>
                </TableRow>
              ))}
              <TableRow>
                <TableCell colSpan={3} className="text-right font-medium">
                  Total
                </TableCell>
                <TableCell className="font-bold">{formatCents(quote.totalCents)}</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Proposal (editable)</CardTitle>
        </CardHeader>
        <CardContent>
          {quote.proposalText ? (
            <ProposalEditor quoteId={quote.id} proposalText={quote.proposalText} />
          ) : (
            <p className="text-sm text-[var(--color-muted-foreground)]">No proposal text.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Status</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {(['sent', 'won', 'lost'] as const).map((s) => (
            <form key={s} action={setQuoteStatusAction}>
              <input type="hidden" name="quoteId" value={quote.id} />
              <input type="hidden" name="status" value={s} />
              <Button type="submit" variant={s === 'won' ? 'default' : 'outline'} size="sm">
                Mark {s}
              </Button>
            </form>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
