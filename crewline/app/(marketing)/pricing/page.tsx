import Link from 'next/link';
import { Sparkles, Check } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

const TIERS = [
  {
    name: 'Starter',
    price: '$99',
    blurb: '1 admin seat, up to ~5 sites',
    features: ['Scheduling + crew app', 'GPS-verified clock-ins', 'AI quotes (capped)', 'Email invoicing'],
  },
  {
    name: 'Pro',
    price: '$249',
    blurb: 'Up to ~25 sites, unlimited crew',
    features: [
      'Everything in Starter',
      'AI client comms + photo checks',
      'Issue triage',
      'Recurring billing',
      'Priority support',
    ],
    featured: true,
  },
  {
    name: 'Scale',
    price: '$499+',
    blurb: '25+ sites, multiple admins',
    features: ['Everything in Pro', 'SMS agent (v1.1)', 'Client portal (v1.1)', 'Advanced reporting'],
  },
];

export default function PricingPage() {
  return (
    <main className="min-h-screen bg-[var(--color-background)]">
      <header className="mx-auto flex max-w-6xl items-center justify-between p-6">
        <Link href="/" className="flex items-center gap-2">
          <Sparkles className="size-6 text-[var(--color-primary)]" />
          <span className="text-lg font-semibold">Crewline</span>
        </Link>
        <Button asChild size="sm">
          <Link href="/login">Sign in</Link>
        </Button>
      </header>
      <section className="mx-auto max-w-5xl px-6 py-12">
        <h1 className="text-center text-3xl font-bold">Simple pricing that scales with your sites</h1>
        <p className="mt-3 text-center text-[var(--color-muted-foreground)]">
          Subscription now; pay-as-you-collect payments later (Act II).
        </p>
        <div className="mt-10 grid gap-6 sm:grid-cols-3">
          {TIERS.map((t) => (
            <Card key={t.name} className={t.featured ? 'border-[var(--color-primary)] shadow-md' : ''}>
              <CardHeader>
                <CardTitle className="flex items-baseline justify-between">
                  <span>{t.name}</span>
                  <span className="text-2xl">
                    {t.price}
                    <span className="text-sm font-normal text-[var(--color-muted-foreground)]">/mo</span>
                  </span>
                </CardTitle>
                <p className="text-sm text-[var(--color-muted-foreground)]">{t.blurb}</p>
              </CardHeader>
              <CardContent className="space-y-3">
                <ul className="space-y-2 text-sm">
                  {t.features.map((f) => (
                    <li key={f} className="flex items-start gap-2">
                      <Check className="mt-0.5 size-4 text-[var(--color-primary)]" />
                      {f}
                    </li>
                  ))}
                </ul>
                <Button asChild className="w-full" variant={t.featured ? 'default' : 'outline'}>
                  <Link href="/signup">Start free</Link>
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>
    </main>
  );
}
