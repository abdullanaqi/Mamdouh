import { requireCrew } from '@/lib/auth/context';

export default async function TodayPage() {
  const auth = await requireCrew();
  return (
    <div>
      <h1 className="text-xl font-bold">Today</h1>
      <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
        Welcome, {auth.fullName}. Your shifts will appear here.
      </p>
    </div>
  );
}
