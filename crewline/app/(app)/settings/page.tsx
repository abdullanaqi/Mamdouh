import { requireOwner } from '@/lib/auth/context';
import { getOrg, listMembers } from '@/lib/domain/org';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { InviteForm } from '@/components/settings/invite-form';

export default async function SettingsPage() {
  const auth = await requireOwner();
  const [org, members] = await Promise.all([getOrg(auth.orgId), listMembers(auth.orgId)]);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Settings</h1>

      <Card>
        <CardHeader>
          <CardTitle>Organization</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2 text-sm sm:grid-cols-3">
          <div>
            <div className="text-xs text-[var(--color-muted-foreground)]">Company</div>
            <div className="font-medium">{org?.name}</div>
          </div>
          <div>
            <div className="text-xs text-[var(--color-muted-foreground)]">Timezone</div>
            <div className="font-medium">{org?.timezone}</div>
          </div>
          <div>
            <div className="text-xs text-[var(--color-muted-foreground)]">Plan</div>
            <Badge className="capitalize">{org?.plan}</Badge>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Team</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Contact</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.map((m) => (
                <TableRow key={m.id}>
                  <TableCell className="font-medium">{m.fullName ?? '—'}</TableCell>
                  <TableCell className="text-[var(--color-muted-foreground)]">
                    {m.email ?? m.phone ?? '—'}
                  </TableCell>
                  <TableCell className="capitalize">{m.role}</TableCell>
                  <TableCell>
                    <Badge variant={m.status === 'active' ? 'success' : 'secondary'}>{m.status}</Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="border-t border-[var(--color-border)] pt-4">
            <h3 className="mb-2 text-sm font-medium">Invite a team member</h3>
            <InviteForm />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Payments (Act II)</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-[var(--color-muted-foreground)]">
          Embedded payments via Stripe Connect arrive in v1.2. Once retention is proven, you&apos;ll
          be able to let clients pay invoices online and have funds land in your account.
        </CardContent>
      </Card>
    </div>
  );
}
