import { z } from 'zod';

/** AI capability I/O schemas (spec §6.2). Outputs are Zod-validated. */

export const confidenceEnum = z.enum(['low', 'medium', 'high']);

// (A) Quote generator
export const quoteInputSchema = z.object({
  siteType: z.enum(['office', 'medical', 'retail', 'school', 'industrial', 'other']),
  squareFootage: z.number().optional(),
  frequency: z.enum(['daily', 'weekly', 'biweekly', 'monthly', 'custom']),
  scopeNotes: z.string(),
  region: z.string().optional(),
  ownerPricingHints: z.string().optional(),
  pastQuotes: z
    .array(z.object({ inputs: z.any(), total: z.number(), won: z.boolean() }))
    .optional(),
});
export type QuoteInput = z.infer<typeof quoteInputSchema>;

export const quoteLineItemSchema = z.object({
  description: z.string(),
  area: z.string().optional(),
  unitBasis: z.string(),
  quantity: z.number(),
  unitPriceCents: z.number(),
  amountCents: z.number(),
});

export const quoteOutputSchema = z.object({
  lineItems: z.array(quoteLineItemSchema).min(1),
  totalCents: z.number(),
  assumptions: z.array(z.string()),
  confidence: confidenceEnum,
});
export type QuoteOutput = z.infer<typeof quoteOutputSchema>;

// (D) Photo quality check
export const photoCheckOutputSchema = z.object({
  pass: z.boolean(),
  reason: z.string(),
  confidence: confidenceEnum,
});
export type PhotoCheckOutput = z.infer<typeof photoCheckOutputSchema>;

// (E) Issue triage
export const issueTriageOutputSchema = z.object({
  severity: z.enum(['low', 'medium', 'high']),
  summary: z.string(),
  suggestedAction: z.string(),
});
export type IssueTriageOutput = z.infer<typeof issueTriageOutputSchema>;
