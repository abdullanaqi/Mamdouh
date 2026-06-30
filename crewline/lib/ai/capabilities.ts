import { runAI } from './client';
import {
  COMMS_SYSTEM,
  ISSUE_TRIAGE_SYSTEM,
  PHOTO_CHECK_SYSTEM,
  PROPOSAL_SYSTEM,
  QUOTE_SYSTEM,
} from './prompts';
import {
  issueTriageOutputSchema,
  photoCheckOutputSchema,
  quoteOutputSchema,
  type IssueTriageOutput,
  type PhotoCheckOutput,
  type QuoteInput,
  type QuoteOutput,
} from './schemas';
import type { AiImageBlock } from './transport';

/** (A) Quote generator — Sonnet 4.6. */
export async function generateQuote(orgId: string, input: QuoteInput): Promise<QuoteOutput> {
  const { data } = await runAI({
    capability: 'quote',
    orgId,
    system: QUOTE_SYSTEM,
    userContent: JSON.stringify(input),
    schema: quoteOutputSchema,
  });
  // Defensive: guarantee line items sum to the total (spec requires it).
  const sum = data.lineItems.reduce((acc, li) => acc + li.amountCents, 0);
  return { ...data, totalCents: sum };
}

/** (B) Proposal writer — Sonnet 4.6. Returns proposal body text. */
export async function writeProposal(
  orgId: string,
  ctx: {
    companyName: string;
    clientName: string;
    siteName?: string;
    siteType?: string;
    frequency: string;
    totalCents: number;
    lineItems: Array<{ description: string; area?: string; amountCents: number }>;
    extras?: string;
  },
): Promise<string> {
  const { text } = await runAI({
    capability: 'proposal',
    orgId,
    system: PROPOSAL_SYSTEM,
    userContent: JSON.stringify(ctx),
  });
  return text.trim();
}

/** (C) Client-comms assistant — Sonnet 4.6. Returns a draft reply. */
export async function draftComms(
  orgId: string,
  ctx: {
    clientName?: string;
    siteName?: string;
    inboundMessage: string;
    relatedIssue?: string;
    relatedShift?: string;
    ownerNotes?: string;
  },
): Promise<string> {
  const { text } = await runAI({
    capability: 'comms_draft',
    orgId,
    system: COMMS_SYSTEM,
    userContent: JSON.stringify(ctx),
  });
  return text.trim();
}

/** (D) Proof-photo quality check — Sonnet 4.6 vision. Advisory only. */
export async function checkPhoto(
  orgId: string,
  args: {
    imageBase64: string;
    mediaType: AiImageBlock['source']['media_type'];
    taskLabel: string;
    siteType?: string;
  },
): Promise<PhotoCheckOutput> {
  const { data } = await runAI({
    capability: 'photo_check',
    orgId,
    system: PHOTO_CHECK_SYSTEM,
    userContent: [
      {
        type: 'image',
        source: { type: 'base64', media_type: args.mediaType, data: args.imageBase64 },
      },
      {
        type: 'text',
        text: `Checklist task: ${args.taskLabel}\nSite type: ${args.siteType ?? 'unknown'}`,
      },
    ],
    schema: photoCheckOutputSchema,
  });
  return data;
}

/** (E) Issue triage — Haiku 4.5. */
export async function triageIssue(
  orgId: string,
  args: { description: string; siteName?: string; source: string },
): Promise<IssueTriageOutput> {
  const { data } = await runAI({
    capability: 'issue_triage',
    orgId,
    system: ISSUE_TRIAGE_SYSTEM,
    userContent: JSON.stringify(args),
    schema: issueTriageOutputSchema,
  });
  return data;
}
