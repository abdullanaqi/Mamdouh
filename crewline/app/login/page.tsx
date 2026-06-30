'use client';
import { useActionState, useState } from 'react';
import { Sparkles } from 'lucide-react';
import {
  loginWithPassword,
  requestCrewOtp,
  verifyCrewOtp,
  type ActionState,
} from '@/app/actions/auth';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

const initial: ActionState = {};

export default function LoginPage() {
  const [tab, setTab] = useState<'owner' | 'crew'>('owner');
  return (
    <main className="flex min-h-screen items-center justify-center bg-[var(--color-muted)] p-4">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-2">
          <Sparkles className="size-6 text-[var(--color-primary)]" />
          <span className="text-xl font-semibold">Crewline</span>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Sign in</CardTitle>
            <CardDescription>
              {tab === 'owner'
                ? 'Owners and admins sign in with email + password.'
                : 'Crew sign in with their phone number.'}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="mb-4 grid grid-cols-2 gap-1 rounded-lg bg-[var(--color-muted)] p-1">
              <button
                type="button"
                onClick={() => setTab('owner')}
                className={`rounded-md py-1.5 text-sm font-medium ${tab === 'owner' ? 'bg-white shadow-sm' : 'text-[var(--color-muted-foreground)]'}`}
              >
                Owner / Admin
              </button>
              <button
                type="button"
                onClick={() => setTab('crew')}
                className={`rounded-md py-1.5 text-sm font-medium ${tab === 'crew' ? 'bg-white shadow-sm' : 'text-[var(--color-muted-foreground)]'}`}
              >
                Crew
              </button>
            </div>
            {tab === 'owner' ? <OwnerForm /> : <CrewForm />}
          </CardContent>
        </Card>
        <p className="mt-4 text-center text-xs text-[var(--color-muted-foreground)]">
          Demo owner: owner@demo.crewline.app / demo1234 · Demo crew: +15555550123
        </p>
      </div>
    </main>
  );
}

function OwnerForm() {
  const [state, action, pending] = useActionState(loginWithPassword, initial);
  return (
    <form action={action} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input id="email" name="email" type="email" placeholder="you@company.com" required />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input id="password" name="password" type="password" required />
      </div>
      {state.error && <p className="text-sm text-[var(--color-destructive)]">{state.error}</p>}
      <Button type="submit" className="w-full" disabled={pending}>
        {pending ? 'Signing in…' : 'Sign in'}
      </Button>
    </form>
  );
}

function CrewForm() {
  const [reqState, reqAction, reqPending] = useActionState(requestCrewOtp, initial);
  const [verState, verAction, verPending] = useActionState(verifyCrewOtp, initial);
  const challenge = reqState.challenge;

  if (!challenge) {
    return (
      <form action={reqAction} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="phone">Phone number</Label>
          <Input id="phone" name="phone" type="tel" placeholder="+15555550123" required />
        </div>
        {reqState.error && (
          <p className="text-sm text-[var(--color-destructive)]">{reqState.error}</p>
        )}
        <Button type="submit" className="w-full" disabled={reqPending}>
          {reqPending ? 'Sending code…' : 'Send code'}
        </Button>
      </form>
    );
  }

  return (
    <form action={verAction} className="space-y-4">
      <input type="hidden" name="challenge" value={challenge} />
      <CarryPhone />
      <div className="rounded-md bg-[var(--color-accent)] p-3 text-sm text-[var(--color-accent-foreground)]">
        Dev mode: your code is <strong>{reqState.devCode}</strong> (no SMS provider configured).
      </div>
      <div className="space-y-2">
        <Label htmlFor="code">Enter 6-digit code</Label>
        <Input id="code" name="code" inputMode="numeric" maxLength={6} placeholder="000000" required />
      </div>
      {verState.error && (
        <p className="text-sm text-[var(--color-destructive)]">{verState.error}</p>
      )}
      <Button type="submit" className="w-full" disabled={verPending}>
        {verPending ? 'Verifying…' : 'Verify & sign in'}
      </Button>
    </form>
  );
}

// The phone field from step 1 lives in a separate form; carry it forward by
// reading the value the user typed (kept simple: re-enter not needed because the
// challenge is bound to the phone server-side — we mirror it from the dev hint).
function CarryPhone() {
  return (
    <div className="space-y-2">
      <Label htmlFor="phone2">Phone number</Label>
      <Input
        id="phone2"
        name="phone"
        type="tel"
        placeholder="+15555550123"
        required
        defaultValue=""
      />
    </div>
  );
}
