import 'server-only';

/**
 * Guard cron endpoints with a shared secret in the `x-cron-secret` header
 * (or `Authorization: Bearer <secret>`, which Vercel Cron sends).
 */
export function authorizeCron(req: Request): boolean {
  const secret = process.env.CRON_SECRET;
  if (!secret) return false;
  const header = req.headers.get('x-cron-secret');
  if (header && header === secret) return true;
  const auth = req.headers.get('authorization');
  if (auth && auth === `Bearer ${secret}`) return true;
  return false;
}
