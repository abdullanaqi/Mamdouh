import 'server-only';
import { getAuth, type AuthContext } from '@/lib/auth/context';
import { DomainError } from '@/lib/domain/errors';

/**
 * Wrap a crew JSON API handler: authenticates, parses JSON, and maps domain
 * errors to HTTP status codes the offline queue understands (4xx = drop,
 * 5xx/throw = retry on next flush).
 */
export async function handleCrew(
  req: Request,
  fn: (body: any, auth: AuthContext) => Promise<unknown>,
): Promise<Response> {
  const auth = await getAuth();
  if (!auth) return Response.json({ error: 'Unauthorized' }, { status: 401 });

  let body: any;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  try {
    const result = await fn(body, auth);
    return Response.json({ ok: true, result });
  } catch (err) {
    if (err instanceof DomainError) {
      const status =
        err.code === 'not_found' ? 404 : err.code === 'forbidden' ? 403 : 422;
      return Response.json({ error: err.message, code: err.code }, { status });
    }
    console.error('crew api error:', err);
    return Response.json({ error: 'Internal error' }, { status: 500 });
  }
}

/** Convert an optional epoch-ms clientTime to a Date (for offline replay accuracy). */
export function clientTimeToDate(v: unknown): Date | undefined {
  if (typeof v === 'number' && Number.isFinite(v)) return new Date(v);
  return undefined;
}
