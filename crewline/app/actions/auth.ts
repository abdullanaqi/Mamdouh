'use server';
import { and, eq } from 'drizzle-orm';
import { redirect } from 'next/navigation';
import { db } from '@/lib/db';
import { orgUsers, users } from '@/lib/db/schema';
import { hashPassword, verifyPassword } from '@/lib/auth/password';
import { setSession, clearSession } from '@/lib/auth/session';
import { orgs } from '@/lib/db/schema';
import {
  createOtpChallenge,
  generateOtpCode,
  verifyOtpChallenge,
} from '@/lib/auth/otp';

export type ActionState = { error?: string; ok?: boolean; challenge?: string; devCode?: string };

async function firstMembership(userId: string) {
  const rows = await db
    .select({ orgId: orgUsers.orgId, role: orgUsers.role, status: orgUsers.status })
    .from(orgUsers)
    .where(and(eq(orgUsers.userId, userId), eq(orgUsers.status, 'active')))
    .limit(1);
  return rows[0] ?? null;
}

/** Owner/admin password login. */
export async function loginWithPassword(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const email = String(formData.get('email') ?? '').trim().toLowerCase();
  const password = String(formData.get('password') ?? '');
  if (!email || !password) return { error: 'Email and password are required.' };

  const rows = await db.select().from(users).where(eq(users.email, email)).limit(1);
  const user = rows[0];
  if (!user || !verifyPassword(password, user.passwordHash)) {
    return { error: 'Invalid email or password.' };
  }
  const membership = await firstMembership(user.id);
  if (!membership) return { error: 'This account is not a member of any organization.' };

  await setSession({
    userId: user.id,
    orgId: membership.orgId,
    role: membership.role as 'owner' | 'admin' | 'cleaner',
  });
  redirect(membership.role === 'cleaner' ? '/today' : '/dashboard');
}

/**
 * Crew OTP step 1: issue a code for a known phone. In the MVP there is no SMS
 * provider (Twilio deferred), so the code is returned for the dev login screen.
 */
export async function requestCrewOtp(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const phone = String(formData.get('phone') ?? '').trim();
  if (!phone) return { error: 'Phone number is required.' };

  const rows = await db.select().from(users).where(eq(users.phone, phone)).limit(1);
  if (!rows[0]) return { error: 'No crew member found with that phone number.' };

  const code = generateOtpCode();
  const challenge = await createOtpChallenge(phone, code);
  console.log(`[otp:dev] code for ${phone} = ${code}`);
  return { ok: true, challenge, devCode: code };
}

/** Crew OTP step 2: verify the code and start a session. */
export async function verifyCrewOtp(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const phone = String(formData.get('phone') ?? '').trim();
  const code = String(formData.get('code') ?? '').trim();
  const challenge = String(formData.get('challenge') ?? '');
  if (!(await verifyOtpChallenge(challenge, phone, code))) {
    return { error: 'Invalid or expired code.' };
  }
  const rows = await db.select().from(users).where(eq(users.phone, phone)).limit(1);
  const user = rows[0];
  if (!user) return { error: 'No crew member found with that phone number.' };
  const membership = await firstMembership(user.id);
  if (!membership) return { error: 'This account is not a member of any organization.' };

  await setSession({
    userId: user.id,
    orgId: membership.orgId,
    role: membership.role as 'owner' | 'admin' | 'cleaner',
  });
  redirect('/today');
}

/** Owner sign-up: creates the user, the org (tenant), and an owner membership. */
export async function signup(_prev: ActionState, formData: FormData): Promise<ActionState> {
  const fullName = String(formData.get('fullName') ?? '').trim();
  const email = String(formData.get('email') ?? '').trim().toLowerCase();
  const password = String(formData.get('password') ?? '');
  const companyName = String(formData.get('companyName') ?? '').trim();
  const timezone = String(formData.get('timezone') ?? 'America/Chicago').trim();

  if (!email || !password || !companyName) {
    return { error: 'Company name, email, and password are required.' };
  }
  if (password.length < 8) return { error: 'Password must be at least 8 characters.' };

  const existing = await db.select({ id: users.id }).from(users).where(eq(users.email, email)).limit(1);
  if (existing[0]) return { error: 'An account with that email already exists.' };

  const { orgId, userId, role } = await db.transaction(async (tx) => {
    const [user] = await tx
      .insert(users)
      .values({ email, fullName: fullName || null, passwordHash: hashPassword(password) })
      .returning();
    const [org] = await tx.insert(orgs).values({ name: companyName, timezone }).returning();
    await tx
      .insert(orgUsers)
      .values({ orgId: org.id, userId: user.id, role: 'owner', status: 'active' });
    return { orgId: org.id, userId: user.id, role: 'owner' as const };
  });

  await setSession({ userId, orgId, role });
  redirect('/dashboard');
}

export async function logout() {
  await clearSession();
  redirect('/login');
}
