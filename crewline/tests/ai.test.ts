import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { and, eq } from 'drizzle-orm';
import { db } from '@/lib/db';
import { aiRuns } from '@/lib/db/schema';
import { resetDb } from '@/lib/db/reset';
import { makeOrg, type TestOrg } from './helpers/factory';
import { setAiTransport, type AiResponse, type AiTransport, type AiRequest } from '@/lib/ai/transport';
import { runAI, AiParseError } from '@/lib/ai/client';
import { computeCostCents } from '@/lib/ai/models';
import { triageIssue, generateQuote } from '@/lib/ai/capabilities';
import { issueTriageOutputSchema } from '@/lib/ai/schemas';

/** Scriptable fake transport: returns queued responses in order. */
class FakeTransport implements AiTransport {
  calls: AiRequest[] = [];
  constructor(private responses: AiResponse[]) {}
  async create(req: AiRequest): Promise<AiResponse> {
    this.calls.push(req);
    const next = this.responses.shift();
    if (!next) throw new Error('FakeTransport: no more responses queued');
    return next;
  }
}

function resp(text: string, input = 1200, output = 80): AiResponse {
  return {
    text,
    usage: {
      input_tokens: input,
      output_tokens: output,
      cache_read_input_tokens: 1000,
      cache_creation_input_tokens: 0,
    },
    model: 'claude-haiku-4-5',
    stop_reason: 'end_turn',
  };
}

let O: TestOrg;

beforeAll(async () => {
  await resetDb();
  O = await makeOrg('AI Org');
});

afterEach(() => setAiTransport(null));

describe('computeCostCents', () => {
  it('prices Haiku input/output correctly', () => {
    // 1M input @ $1 + 1M output @ $5 = $6 = 600 cents (no cache).
    const cents = computeCostCents('claude-haiku-4-5', {
      inputTokens: 1_000_000,
      outputTokens: 1_000_000,
      cacheReadTokens: 0,
      cacheCreationTokens: 0,
    });
    expect(cents).toBeCloseTo(600, 2);
  });
});

describe('runAI', () => {
  it('logs tokens + cost to ai_runs on a successful call', async () => {
    setAiTransport(new FakeTransport([resp(JSON.stringify({ severity: 'low', summary: 's', suggestedAction: 'a' }))]));
    const res = await runAI({
      capability: 'issue_triage',
      orgId: O.orgId,
      system: 'sys',
      userContent: 'hello',
      schema: issueTriageOutputSchema,
    });
    expect(res.data.severity).toBe('low');
    expect(res.costCents).toBeGreaterThan(0);

    const runs = await db
      .select()
      .from(aiRuns)
      .where(and(eq(aiRuns.orgId, O.orgId), eq(aiRuns.capability, 'issue_triage')));
    expect(runs.length).toBe(1);
    expect(runs[0].inputTokens).toBe(1200);
    expect(runs[0].outputTokens).toBe(80);
    expect(runs[0].ok).toBe(true);
    expect(Number(runs[0].costCents)).toBeGreaterThan(0);
  });

  it('repairs once when the first response is invalid JSON, then succeeds', async () => {
    const fake = new FakeTransport([
      resp('here is your answer: not json at all'),
      resp(JSON.stringify({ severity: 'high', summary: 'fixed', suggestedAction: 'do it' })),
    ]);
    setAiTransport(fake);
    const res = await runAI({
      capability: 'issue_triage',
      orgId: O.orgId,
      system: 'sys',
      userContent: 'broken first',
      schema: issueTriageOutputSchema,
    });
    expect(res.data.summary).toBe('fixed');
    expect(fake.calls.length).toBe(2); // initial + one repair
  });

  it('throws AiParseError and logs ok=false when repair also fails', async () => {
    setAiTransport(new FakeTransport([resp('garbage one'), resp('garbage two')]));
    await expect(
      runAI({
        capability: 'issue_triage',
        orgId: O.orgId,
        system: 'sys',
        userContent: 'both bad',
        schema: issueTriageOutputSchema,
      }),
    ).rejects.toBeInstanceOf(AiParseError);

    const failed = await db
      .select()
      .from(aiRuns)
      .where(and(eq(aiRuns.orgId, O.orgId), eq(aiRuns.ok, false)));
    expect(failed.length).toBeGreaterThanOrEqual(1);
  });

  it('tolerates code-fenced JSON', async () => {
    setAiTransport(
      new FakeTransport([resp('```json\n{"severity":"medium","summary":"x","suggestedAction":"y"}\n```')]),
    );
    const out = await triageIssue(O.orgId, { description: 'spill', source: 'crew' });
    expect(out.severity).toBe('medium');
  });
});

describe('generateQuote', () => {
  it('forces line items to sum to the total', async () => {
    // AI returns an inconsistent total; capability recomputes from line items.
    setAiTransport(
      new FakeTransport([
        resp(
          JSON.stringify({
            lineItems: [
              { description: 'Restrooms', unitBasis: 'per visit', quantity: 1, unitPriceCents: 5000, amountCents: 5000 },
              { description: 'Floors', unitBasis: 'per visit', quantity: 1, unitPriceCents: 7000, amountCents: 7000 },
            ],
            totalCents: 999999, // wrong on purpose
            assumptions: ['Assumed 5000 sq ft'],
            confidence: 'medium',
          }),
        ),
      ]),
    );
    const quote = await generateQuote(O.orgId, {
      siteType: 'office',
      frequency: 'weekly',
      scopeNotes: 'standard office',
    });
    expect(quote.totalCents).toBe(12000);
    expect(quote.lineItems.length).toBe(2);
  });
});
