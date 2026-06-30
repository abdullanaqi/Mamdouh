import { randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';

/**
 * Password hashing via Node's built-in scrypt (no native dependency).
 * Format: scrypt$<saltHex>$<hashHex>
 *
 * MVP note: the spec calls for Supabase Auth, which requires a hosted Supabase
 * project (a credential only the human can provision — see HANDBACK). This
 * self-contained layer keeps the same shape (password for owners/admins, phone
 * OTP for crew) so the app is runnable and testable locally; swapping in a
 * Supabase adapter later does not touch the domain layer.
 */
const KEYLEN = 64;

export function hashPassword(password: string): string {
  const salt = randomBytes(16);
  const derived = scryptSync(password, salt, KEYLEN);
  return `scrypt$${salt.toString('hex')}$${derived.toString('hex')}`;
}

export function verifyPassword(password: string, stored: string | null | undefined): boolean {
  if (!stored) return false;
  const parts = stored.split('$');
  if (parts.length !== 3 || parts[0] !== 'scrypt') return false;
  const salt = Buffer.from(parts[1], 'hex');
  const expected = Buffer.from(parts[2], 'hex');
  const derived = scryptSync(password, salt, expected.length);
  if (derived.length !== expected.length) return false;
  return timingSafeEqual(derived, expected);
}
