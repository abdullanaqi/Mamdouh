'use client';
import { useActionState } from 'react';
import Link from 'next/link';
import { Sparkles } from 'lucide-react';
import { signup, type ActionState } from '@/app/actions/auth';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

const initial: ActionState = {};
const TIMEZONES = [
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Phoenix',
];

export default function SignupPage() {
  const [state, action, pending] = useActionState(signup, initial);
  return (
    <main className="flex min-h-screen items-center justify-center bg-[var(--color-muted)] p-4">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-2">
          <Sparkles className="size-6 text-[var(--color-primary)]" />
          <span className="text-xl font-semibold">Crewline</span>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Create your company</CardTitle>
            <CardDescription>Start free. Set up your first site in minutes.</CardDescription>
          </CardHeader>
          <CardContent>
            <form action={action} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="companyName">Company name</Label>
                <Input id="companyName" name="companyName" required placeholder="Sparkle Pro Cleaning" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="timezone">Timezone</Label>
                <Select id="timezone" name="timezone" defaultValue="America/Chicago">
                  {TIMEZONES.map((tz) => (
                    <option key={tz} value={tz}>
                      {tz}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="fullName">Your name</Label>
                <Input id="fullName" name="fullName" placeholder="Dana Owner" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input id="email" name="email" type="email" required placeholder="you@company.com" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <Input id="password" name="password" type="password" required minLength={8} />
              </div>
              {state.error && (
                <p className="text-sm text-[var(--color-destructive)]">{state.error}</p>
              )}
              <Button type="submit" className="w-full" disabled={pending}>
                {pending ? 'Creating…' : 'Create company'}
              </Button>
            </form>
          </CardContent>
        </Card>
        <p className="mt-4 text-center text-sm text-[var(--color-muted-foreground)]">
          Already have an account?{' '}
          <Link href="/login" className="text-[var(--color-primary)] hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </main>
  );
}
