/**
 * DEFERRED — Phase 4 / v1.1 (spec §7.2). Twilio SMS/Voice + crew OTP delivery
 * + inbound SMS agent. This file is a scaffolded interface ONLY. It is NOT
 * implemented in the MVP (Phases 0-3) and requires a real Twilio account
 * (HANDBACK). Do not call these in MVP code paths.
 */
export interface TwilioGateway {
  sendSms(to: string, body: string): Promise<{ sid: string }>;
  startVerification(phone: string): Promise<{ status: string }>;
  checkVerification(phone: string, code: string): Promise<{ valid: boolean }>;
}

export function getTwilioGateway(): TwilioGateway {
  throw new Error(
    'Twilio is a deferred Phase 4 integration and is not implemented in the MVP. ' +
      'Provision a Twilio account and implement TwilioGateway before enabling SMS features.',
  );
}
