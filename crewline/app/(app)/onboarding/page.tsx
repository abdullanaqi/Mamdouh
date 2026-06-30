import Link from 'next/link';
import { CheckCircle2, Circle, ArrowRight, Sparkles } from 'lucide-react';
import { requireOwner } from '@/lib/auth/context';
import { listClients } from '@/lib/domain/clients';
import { listSites } from '@/lib/domain/sites';
import { listMembers } from '@/lib/domain/org';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

export default async function OnboardingPage() {
  const auth = await requireOwner();
  const [clients, sites, members] = await Promise.all([
    listClients(auth.orgId),
    listSites(auth.orgId),
    listMembers(auth.orgId),
  ]);
  const hasClient = clients.length > 0;
  const hasSite = sites.length > 0;
  const hasCrew = members.some((m) => m.role === 'cleaner');
  const done = [hasClient, hasSite, hasCrew].filter(Boolean).length;

  const steps = [
    {
      done: hasClient,
      title: 'Add your first client',
      body: 'The business you clean for (e.g. a dental office).',
      href: '/clients/new',
      cta: 'Add client',
    },
    {
      done: hasSite,
      title: 'Add a site with its checklist',
      body: 'A location under that client, with address, geofence, and tasks.',
      href: hasClient ? `/sites/new?clientId=${clients[0].id}` : '/clients/new',
      cta: 'Add site',
    },
    {
      done: hasCrew,
      title: 'Invite a crew member',
      body: 'They sign in on their phone and clock in at the job site.',
      href: '/settings',
      cta: 'Invite crew',
    },
  ];

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className="flex items-center gap-2">
        <Sparkles className="size-6 text-[var(--color-primary)]" />
        <h1 className="text-2xl font-bold">Welcome to Crewline</h1>
      </div>
      <p className="text-[var(--color-muted-foreground)]">
        Three quick steps to your first verified shift. {done}/3 done.
      </p>

      <div className="space-y-3">
        {steps.map((s) => (
          <Card key={s.title} className={s.done ? 'opacity-70' : ''}>
            <CardContent className="flex items-center justify-between gap-4 pt-6">
              <div className="flex items-start gap-3">
                {s.done ? (
                  <CheckCircle2 className="mt-0.5 size-5 text-[var(--color-success)]" />
                ) : (
                  <Circle className="mt-0.5 size-5 text-[var(--color-muted-foreground)]" />
                )}
                <div>
                  <div className="font-medium">{s.title}</div>
                  <div className="text-sm text-[var(--color-muted-foreground)]">{s.body}</div>
                </div>
              </div>
              {!s.done && (
                <Button asChild size="sm">
                  <Link href={s.href}>
                    {s.cta} <ArrowRight className="size-4" />
                  </Link>
                </Button>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      {done === 3 && (
        <Card className="border-[var(--color-primary)]">
          <CardHeader>
            <CardTitle>You&apos;re set up! 🎉</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm">
              Schedule a shift, then have your crew clock in on their phone to see verification in action.
            </p>
            <div className="flex gap-2">
              <Button asChild>
                <Link href="/schedule">Open schedule</Link>
              </Button>
              <Button asChild variant="outline">
                <Link href="/dashboard">Go to dashboard</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
