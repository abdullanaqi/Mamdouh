import 'server-only';
import { redirect } from 'next/navigation';
import { and, eq } from 'drizzle-orm';
import { db } from '@/lib/db';
import { orgUsers, users } from '@/lib/db/schema';
import { getSession, type SessionPayload } from './session';

export type AuthContext = {
  userId: string;
  orgId: string;
  role: 'owner' | 'admin' | 'cleaner';
  fullName: string | null;
  email: string | null;
};

/**
 * Resolve the session AND re-verify the membership against the DB (so a revoked
 * or disabled membership can't keep acting on a stale cookie). Returns null if
 * not authenticated or membership is no longer active.
 */
export async function getAuth(): Promise<AuthContext | null> {
  const session = await getSession();
  if (!session) return null;
  return resolveAuth(session);
}

async function resolveAuth(session: SessionPayload): Promise<AuthContext | null> {
  const rows = await db
    .select({
      role: orgUsers.role,
      status: orgUsers.status,
      fullName: users.fullName,
      email: users.email,
    })
    .from(orgUsers)
    .innerJoin(users, eq(users.id, orgUsers.userId))
    .where(and(eq(orgUsers.userId, session.userId), eq(orgUsers.orgId, session.orgId)))
    .limit(1);

  const m = rows[0];
  if (!m || m.status !== 'active') return null;

  return {
    userId: session.userId,
    orgId: session.orgId,
    role: m.role as AuthContext['role'],
    fullName: m.fullName,
    email: m.email,
  };
}

/** Owner/admin web app guard. Redirects to /login when unauthenticated. */
export async function requireOwner(): Promise<AuthContext> {
  const auth = await getAuth();
  if (!auth) redirect('/login');
  if (auth.role !== 'owner' && auth.role !== 'admin') redirect('/today');
  return auth;
}

/** Crew PWA guard. Redirects to /login when unauthenticated. */
export async function requireCrew(): Promise<AuthContext> {
  const auth = await getAuth();
  if (!auth) redirect('/login');
  return auth;
}

/** Any authenticated member. */
export async function requireAuth(): Promise<AuthContext> {
  const auth = await getAuth();
  if (!auth) redirect('/login');
  return auth;
}
