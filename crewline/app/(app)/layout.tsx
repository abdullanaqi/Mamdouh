import { requireOwner } from '@/lib/auth/context';
import { AppNav } from '@/components/app-nav';
import { logout } from '@/app/actions/auth';
import { Button } from '@/components/ui/button';
import { LogOut } from 'lucide-react';

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const auth = await requireOwner();
  return (
    <div className="flex min-h-screen bg-[var(--color-muted)]">
      <aside className="hidden w-60 shrink-0 border-r border-[var(--color-border)] bg-[var(--color-background)] md:flex md:flex-col md:justify-between">
        <AppNav />
        <div className="border-t border-[var(--color-border)] p-3">
          <div className="px-3 py-2 text-sm">
            <div className="font-medium">{auth.fullName ?? auth.email}</div>
            <div className="text-xs capitalize text-[var(--color-muted-foreground)]">{auth.role}</div>
          </div>
          <form action={logout}>
            <Button type="submit" variant="ghost" size="sm" className="w-full justify-start">
              <LogOut className="size-4" /> Sign out
            </Button>
          </form>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile top bar */}
        <header className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-background)] p-3 md:hidden">
          <span className="font-semibold">Crewline</span>
          <form action={logout}>
            <Button type="submit" variant="ghost" size="sm">
              <LogOut className="size-4" />
            </Button>
          </form>
        </header>
        <main className="min-w-0 flex-1 p-4 sm:p-6 lg:p-8">{children}</main>
      </div>
    </div>
  );
}
