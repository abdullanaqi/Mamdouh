import { requireOwner } from '@/lib/auth/context';

export default async function DashboardPage() {
  const auth = await requireOwner();
  return (
    <div>
      <h1 className="text-2xl font-bold">Dashboard</h1>
      <p className="mt-2 text-[var(--color-muted-foreground)]">
        Signed in as {auth.fullName ?? auth.email} ({auth.role}).
      </p>
    </div>
  );
}
