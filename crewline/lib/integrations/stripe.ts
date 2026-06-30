/**
 * DEFERRED — Phase 5 / v1.2, "Act II" embedded payments (spec §7.1).
 * Stripe Connect: platform + connected accounts, destination charges with an
 * application fee, webhooks. This file is a scaffolded interface ONLY. It is
 * NOT implemented in the MVP and requires real Stripe keys + a verified
 * platform account (HANDBACK).
 *
 * The DB already carries `stripe_accounts` and `invoices.stripe_payment_intent_id`
 * so this can be layered in without a migration that touches existing data.
 */
export interface StripeConnectGateway {
  createConnectedAccount(orgId: string): Promise<{ accountId: string }>;
  createOnboardingLink(accountId: string): Promise<{ url: string }>;
  createInvoicePaymentIntent(args: {
    invoiceId: string;
    amountCents: number;
    connectedAccountId: string;
    applicationFeeCents: number;
  }): Promise<{ clientSecret: string; paymentIntentId: string }>;
  handleWebhook(payload: string, signature: string): Promise<{ type: string }>;
}

export function getStripeGateway(): StripeConnectGateway {
  throw new Error(
    'Stripe Connect is a deferred Phase 5 (Act II) integration and is not implemented in the MVP. ' +
      'Provision Stripe keys and implement StripeConnectGateway before enabling payments.',
  );
}
