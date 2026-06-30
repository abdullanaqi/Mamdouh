'use client';
import { useEffect } from 'react';
import { captureError } from '@/lib/observability';
import { Button } from '@/components/ui/button';

export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    captureError(error, { digest: error.digest });
  }, [error]);

  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <h2 className="text-xl font-bold">Something went wrong</h2>
      <p className="mt-2 text-sm text-[var(--color-muted-foreground)]">
        We hit an unexpected error. You can try again, or head back to the dashboard.
      </p>
      <div className="mt-4 flex justify-center gap-2">
        <Button onClick={reset}>Try again</Button>
        <Button variant="outline" asChild>
          <a href="/dashboard">Dashboard</a>
        </Button>
      </div>
    </div>
  );
}
