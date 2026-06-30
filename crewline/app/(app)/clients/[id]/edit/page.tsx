import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { getClient } from '@/lib/domain/clients';
import { ClientForm } from '@/components/forms/client-form';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default async function EditClientPage({ params }: { params: Promise<{ id: string }> }) {
  const auth = await requireOwner();
  const { id } = await params;
  const client = await getClient(auth.orgId, id);
  if (!client) notFound();

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <Link href={`/clients/${id}`} className="flex items-center gap-1 text-sm text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
        <ArrowLeft className="size-4" /> Back
      </Link>
      <Card>
        <CardHeader>
          <CardTitle>Edit client</CardTitle>
        </CardHeader>
        <CardContent>
          <ClientForm client={client} />
        </CardContent>
      </Card>
    </div>
  );
}
