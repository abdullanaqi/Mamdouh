import { SignJWT, jwtVerify } from 'jose';
import { createHash, randomInt } from 'node:crypto';

/**
 * Stateless phone OTP for crew login.
 *
 * A challenge is a short-lived signed JWT carrying the phone and a hash of the
 * 6-digit code (never the code itself). The code is delivered out-of-band; in
 * the MVP there is no SMS provider (Twilio is deferred — see HANDBACK), so the
 * code is surfaced to the developer via the dev login screen / server log.
 * Real SMS delivery is a Phase 4 / HANDBACK item.
 */
const ALG = 'HS256';
const TTL_SECONDS = 300; // 5 minutes

function secret(): Uint8Array {
  const s = process.env.AUTH_SECRET;
  if (!s || s.length < 16) throw new Error('AUTH_SECRET is not set (min 16 chars).');
  return new TextEncoder().encode(s);
}

function hashCode(phone: string, code: string): string {
  return createHash('sha256').update(`${phone}:${code}`).digest('hex');
}

export function generateOtpCode(): string {
  return String(randomInt(0, 1_000_000)).padStart(6, '0');
}

export async function createOtpChallenge(
  phone: string,
  code: string,
): Promise<string> {
  return new SignJWT({ phone, codeHash: hashCode(phone, code) })
    .setProtectedHeader({ alg: ALG })
    .setIssuedAt()
    .setExpirationTime(`${TTL_SECONDS}s`)
    .sign(secret());
}

export async function verifyOtpChallenge(
  challenge: string,
  phone: string,
  code: string,
): Promise<boolean> {
  try {
    const { payload } = await jwtVerify(challenge, secret());
    return payload.phone === phone && payload.codeHash === hashCode(phone, code);
  } catch {
    return false;
  }
}
