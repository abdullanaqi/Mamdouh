import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { resetDb } from '@/lib/db/reset';
import { makeOrg, type TestOrg } from './helpers/factory';
import { setAiTransport, type AiResponse, type AiTransport, type AiRequest } from '@/lib/ai/transport';
import { generateQuote, writeProposal } from '@/lib/ai/capabilities';
import { createQuote, getQuote, setQuoteStatus, pastQuotesForCalibration } from '@/lib/domain/quotes';

class ScriptedTransport implements AiTransport {
  constructor(private responses: AiResponse[]) {}
  async create(_req: AiRequest): Promise<AiResponse> {
    const r = this.responses.shift();
    if (!r) throw new Error('no response queued');
    return r;
  }
}
const resp = (text: string): AiResponse => ({
  text,
  usage: { input_tokens: 500, output_tokens: 100, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
  model: 'claude-sonnet-4-6',
  stop_reason: 'end_turn',
});

let O: TestOrg;
beforeAll(async () => {
  await resetDb();
  O = await makeOrg('Quote Org');
});
afterEach(() => setAiTransport(null));

describe('quote pipeline (generate → proposal → persist → read)', () => {
  it('persists an itemized quote and editable proposal', async () => {
    setAiTransport(
      new ScriptedTransport([
        resp(
          JSON.stringify({
            lineItems: [
              { description: 'Restrooms (2)', area: 'Restrooms', unitBasis: 'per visit', quantity: 1, unitPriceCents: 6000, amountCents: 6000 },
              { description: 'Operatory disinfection', area: 'Operatories', unitBasis: 'per visit', quantity: 1, unitPriceCents: 9000, amountCents: 9000 },
            ],
            totalCents: 15000,
            assumptions: ['Assumed 6000 sq ft medical office'],
            confidence: 'high',
          }),
        ),
        resp('Dear Acme Dental, we are delighted to propose nightly cleaning…'),
      ]),
    );

    const ai = await generateQuote(O.orgId, {
      siteType: 'medical',
      frequency: 'weekly',
      squareFootage: 6000,
      scopeNotes: '2 restrooms + 4 operatories, nightly trash, weekly disinfection',
    });
    expect(ai.totalCents).toBe(15000);

    const proposal = await writeProposal(O.orgId, {
      companyName: 'Sparkle Pro',
      clientName: 'Acme Dental',
      frequency: 'weekly',
      totalCents: ai.totalCents,
      lineItems: ai.lineItems.map((li) => ({ description: li.description, amountCents: li.amountCents })),
    });
    expect(proposal.length).toBeGreaterThan(0);

    const quote = await createQuote(O.orgId, {
      prospectName: 'Acme Dental',
      frequency: 'weekly',
      inputsJson: { siteType: 'medical' },
      lineItems: ai.lineItems,
      totalCents: ai.totalCents,
      proposalText: proposal,
    });

    const loaded = await getQuote(O.orgId, quote.id);
    expect(loaded?.totalCents).toBe(15000);
    expect((loaded?.lineItemsJson as any[]).length).toBe(2);
    expect(loaded?.proposalText).toContain('Acme Dental');
    expect(loaded?.status).toBe('draft');
  });

  it('feeds won/lost history into calibration', async () => {
    const q = await createQuote(O.orgId, {
      prospectName: 'History Co',
      frequency: 'monthly',
      inputsJson: { siteType: 'office' },
      lineItems: [{ description: 'x', unitBasis: 'per month', quantity: 1, unitPriceCents: 1000, amountCents: 1000 }],
      totalCents: 1000,
    });
    await setQuoteStatus(O.orgId, q.id, 'won');
    const past = await pastQuotesForCalibration(O.orgId);
    expect(past.some((p) => p.won && p.total === 1000)).toBe(true);
  });

  it('is org-scoped: cannot read another org quote', async () => {
    const other = await makeOrg('Other Quote Org');
    const q = await createQuote(O.orgId, {
      prospectName: 'Mine',
      frequency: 'weekly',
      inputsJson: {},
      lineItems: [{ description: 'x', unitBasis: 'visit', quantity: 1, unitPriceCents: 1, amountCents: 1 }],
      totalCents: 1,
    });
    expect(await getQuote(other.orgId, q.id)).toBeNull();
  });
});
