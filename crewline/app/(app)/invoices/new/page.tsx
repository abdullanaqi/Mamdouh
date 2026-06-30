import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { listSitesForInvoicing } from '@/lib/domain/invoices';
import { InvoiceForm } from '@/components/forms/invoice-form';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default async function NewInvoicePage() {
  const auth = await requireOwner();
  const sites = await listSitesForInvoicing(auth.orgId);
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <Link href="/invoices" className="flex items-center gap-1 text-sm text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
        <ArrowLeft className="size-4" /> Back to invoices
      </Link>
      <Card>
        <CardHeader>
          <CardTitle>Generate invoice</CardTitle>
        </CardHeader>
        <CardContent>
          <InvoiceForm sites={sites} />
        </CardContent>
      </Card>
    </div>
  );
}
