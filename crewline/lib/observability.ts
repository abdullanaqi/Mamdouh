/**
 * Observability scaffolding (spec §3.5, T3.4). Interfaces are wired now; the
 * concrete SDKs (Sentry, PostHog) activate only when their env vars are present.
 *
 * To go live: set SENTRY_DSN and NEXT_PUBLIC_POSTHOG_KEY/HOST, then install and
 * initialize @sentry/nextjs and posthog-js (HANDBACK — needs the real DSN/key).
 * Until then these are safe no-ops so the app runs without the dependencies.
 */
export function captureError(error: unknown, context?: Record<string, unknown>): void {
  const dsn = process.env.SENTRY_DSN;
  if (!dsn) {
    // No Sentry configured — log locally so errors aren't swallowed silently.
    console.error('[observability] captureError', error, context ?? '');
    return;
  }
  // Real Sentry capture would go here once @sentry/nextjs is initialized.
  console.error('[sentry] captureError', error, context ?? '');
}

export function analyticsEnabled(): boolean {
  return Boolean(process.env.NEXT_PUBLIC_POSTHOG_KEY);
}
