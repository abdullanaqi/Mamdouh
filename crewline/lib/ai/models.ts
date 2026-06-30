/**
 * Model routing + pricing (spec §3.3, §6.1). Model IDs are the spec's verified
 * mid-2026 values. Pricing is USD per 1M tokens, also from the spec.
 */
export type Capability =
  | 'quote'
  | 'proposal'
  | 'comms_draft'
  | 'photo_check'
  | 'issue_triage'
  | 'sms_agent';

export const MODEL_ROUTING: Record<Capability, string> = {
  quote: 'claude-sonnet-4-6',
  proposal: 'claude-sonnet-4-6',
  comms_draft: 'claude-sonnet-4-6',
  issue_triage: 'claude-haiku-4-5',
  photo_check: 'claude-sonnet-4-6', // vision
  sms_agent: 'claude-haiku-4-5',
};

/** Per-1M-token prices (USD), spec §3.3. */
export const MODEL_PRICING: Record<string, { inPerM: number; outPerM: number }> = {
  'claude-haiku-4-5': { inPerM: 1, outPerM: 5 },
  'claude-sonnet-4-6': { inPerM: 3, outPerM: 15 },
  'claude-opus-4-8': { inPerM: 5, outPerM: 25 },
};

export const MAX_TOKENS: Record<Capability, number> = {
  quote: 2000,
  proposal: 1500,
  comms_draft: 800,
  photo_check: 400,
  issue_triage: 300,
  sms_agent: 300,
};

export type TokenUsage = {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
};

/**
 * Compute the cost in cents for a call.
 *
 * // VERIFY: cache pricing multipliers. Anthropic prompt caching bills cache
 * // *writes* above the base input rate and cache *reads* well below it ("up to
 * // ~90% off" per the spec). The exact multipliers (commonly cited as 1.25x
 * // write / 0.1x read) should be confirmed against current Anthropic docs
 * // before relying on these numbers for billing. They are used only for the
 * // internal ai_runs cost view, not customer billing.
 */
export function computeCostCents(model: string, usage: TokenUsage): number {
  const p = MODEL_PRICING[model];
  if (!p) return 0;
  const CACHE_WRITE_MULT = 1.25;
  const CACHE_READ_MULT = 0.1;
  const inputCost = (usage.inputTokens / 1_000_000) * p.inPerM;
  const cacheWriteCost = (usage.cacheCreationTokens / 1_000_000) * p.inPerM * CACHE_WRITE_MULT;
  const cacheReadCost = (usage.cacheReadTokens / 1_000_000) * p.inPerM * CACHE_READ_MULT;
  const outputCost = (usage.outputTokens / 1_000_000) * p.outPerM;
  return (inputCost + cacheWriteCost + cacheReadCost + outputCost) * 100;
}
