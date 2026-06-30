import Link from 'next/link';
import { FileText, Plus } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { listQuotes } from '@/lib/domain/quotes';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { EmptyState } from '@/components/empty-state';
import { formatCents } from '@/lib/utils';

export default async function QuotesPage() {
  const auth = await requireOwner();
  const quotes = await listQuotes(auth.orgId);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Quotes</h1>
        <Button asChild>
          <Link href="/quotes/new">
            <Plus className="size-4" /> New quote
          </Link>
        </Button>
      </div>
      <Card>
        <CardContent className="pt-6">
          {quotes.length === 0 ? (
            <EmptyState
              icon={<FileText className="size-8" />}
              title="No quotes yet"
              body="Describe a site and let AI draft an itemized, professional quote in seconds."
              action={<Link href="/quotes/new" className="text-[var(--color-primary)] hover:underline">Create a quote</Link>}
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Prospect / Client</TableHead>
                  <TableHead>Total</TableHead>
                  <TableHead>Frequency</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {quotes.map((q) => (
                  <TableRow key={q.id}>
                    <TableCell>
                      <Link href={`/quotes/${q.id}`} className="font-medium hover:underline">
                        {q.prospectName ?? q.clientName ?? 'Untitled'}
                      </Link>
                    </TableCell>
                    <TableCell>{formatCents(q.totalCents)}</TableCell>
                    <TableCell className="capitalize">{q.frequency}</TableCell>
                    <TableCell>
                      <Badge variant={q.status === 'won' ? 'success' : q.status === 'lost' ? 'destructive' : 'secondary'}>
                        {q.status}
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
