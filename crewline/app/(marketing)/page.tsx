import Link from 'next/link';
import { Sparkles, MapPin, FileText, CreditCard, CheckCircle2 } from 'lucide-react';
import { Button } from '@/components/ui/button';

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-[var(--color-background)]">
      <header className="mx-auto flex max-w-6xl items-center justify-between p-6">
        <div className="flex items-center gap-2">
          <Sparkles className="size-6 text-[var(--color-primary)]" />
          <span className="text-lg font-semibold">Crewline</span>
        </div>
        <nav className="flex items-center gap-4 text-sm">
          <Link href="/pricing" className="text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
            Pricing
          </Link>
          <Button asChild size="sm">
            <Link href="/login">Sign in</Link>
          </Button>
        </nav>
      </header>

      <section className="mx-auto max-w-3xl px-6 py-20 text-center">
        <h1 className="text-balance text-4xl font-bold tracking-tight sm:text-5xl">
          Know your crews showed up. Prove the work got done. Get paid faster.
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg text-[var(--color-muted-foreground)]">
          Crewline is the all-in-one app for commercial cleaning companies — GPS-verified
          clock-ins, photo proof of every job, AI-built quotes, and one-tap invoicing. Built for
          the field, not the back office.
        </p>
        <div className="mt-8 flex items-center justify-center gap-3">
          <Button asChild size="lg">
            <Link href="/signup">Start free</Link>
          </Button>
          <Button asChild size="lg" variant="outline">
            <Link href="/login">Book a 15-min demo</Link>
          </Button>
        </div>
      </section>

      <section className="mx-auto grid max-w-5xl gap-6 px-6 pb-20 sm:grid-cols-3">
        <ValueProp
          icon={<MapPin className="size-5 text-[var(--color-primary)]" />}
          title="Verified service, automatically"
          body="GPS clock-ins and photo proof for every site, so you and your clients know the job got done — no more “did they actually show up?”"
        />
        <ValueProp
          icon={<FileText className="size-5 text-[var(--color-primary)]" />}
          title="Quote in minutes, not hours"
          body="Describe the site; our AI writes an itemized, professional quote and proposal you can send on the spot."
        />
        <ValueProp
          icon={<CreditCard className="size-5 text-[var(--color-primary)]" />}
          title="Get paid without the chase"
          body="Turn completed jobs into invoices in one tap; soon, let clients pay online and money lands in your account."
        />
      </section>

      <footer className="border-t border-[var(--color-border)] py-8 text-center text-sm text-[var(--color-muted-foreground)]">
        <p className="flex items-center justify-center gap-1">
          <CheckCircle2 className="size-4" /> Built with cleaning companies, for cleaning companies.
        </p>
        <p className="mt-2">© {new Date().getFullYear()} Crewline</p>
      </footer>
    </main>
  );
}

function ValueProp({ icon, title, body }: { icon: React.ReactNode; title: string; body: string }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6">
      <div className="mb-3 flex size-10 items-center justify-center rounded-lg bg-[var(--color-accent)]">
        {icon}
      </div>
      <h3 className="font-semibold">{title}</h3>
      <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">{body}</p>
    </div>
  );
}
