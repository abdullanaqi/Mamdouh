import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { ClientForm } from '@/components/forms/client-form';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default async function NewClientPage() {
  await requireOwner();
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <Link href="/clients" className="flex items-center gap-1 text-sm text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
        <ArrowLeft className="size-4" /> Back to clients
      </Link>
      <Card>
        <CardHeader>
          <CardTitle>New client</CardTitle>
        </CardHeader>
        <CardContent>
          <ClientForm />
        </CardContent>
      </Card>
    </div>
  );
}
