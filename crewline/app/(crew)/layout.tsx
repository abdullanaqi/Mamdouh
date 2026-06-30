import Link from 'next/link';
import { Sparkles, ClipboardList, History, LogOut } from 'lucide-react';
import { requireCrew } from '@/lib/auth/context';
import { logout } from '@/app/actions/auth';
import { Button } from '@/components/ui/button';
import { CrewServiceWorker } from '@/components/crew-sw';

export default async function CrewLayout({ children }: { children: React.ReactNode }) {
  const auth = await requireCrew();
  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col bg-[var(--color-background)]">
      <CrewServiceWorker />
      <header className="flex items-center justify-between border-b border-[var(--color-border)] p-4">
        <div className="flex items-center gap-2">
          <Sparkles className="size-5 text-[var(--color-primary)]" />
          <span className="font-semibold">Crewline</span>
        </div>
        <span className="text-sm text-[var(--color-muted-foreground)]">{auth.fullName}</span>
      </header>

      <main className="flex-1 p-4 pb-24">{children}</main>

      <nav className="fixed inset-x-0 bottom-0 mx-auto flex max-w-md items-center justify-around border-t border-[var(--color-border)] bg-[var(--color-background)] p-2">
        <Link href="/today" className="crew-tap flex flex-1 flex-col items-center justify-center gap-1 rounded-md text-xs">
          <ClipboardList className="size-5" /> Today
        </Link>
        <Link href="/history" className="crew-tap flex flex-1 flex-col items-center justify-center gap-1 rounded-md text-xs">
          <History className="size-5" /> History
        </Link>
        <form action={logout} className="flex-1">
          <Button type="submit" variant="ghost" className="crew-tap flex w-full flex-col items-center justify-center gap-1 text-xs">
            <LogOut className="size-5" /> Sign out
          </Button>
        </form>
      </nav>
    </div>
  );
}
