import { z } from 'zod';
import { db } from '@/lib/db';
import { aiRuns } from '@/lib/db/schema';
import {
  MAX_TOKENS,
  MODEL_ROUTING,
  computeCostCents,
  type Capability,
} from './models';
import { getAiTransport, type AiContentBlock } from './transport';

/**
 * Central AI entry point (spec §6.1). Routes the model, injects a prompt-cached
 * system prompt, calls the transport, optionally parses + Zod-validates JSON
 * with one repair retry, and logs every call to ai_runs (tokens, cost, latency).
 */
export type RunAIArgs<T> = {
  capability: Capability;
  orgId: string;
  system: string; // static instructional prompt (cached)
  userContent: string | AiContentBlock[];
  schema?: z.ZodType<T>;
  maxTokens?: number;
  temperature?: number;
};

export type RunAIResult<T> = {
  data: T;
  text: string;
  costCents: number;
  model: string;
};

/** Strip accidental ```json fences or prose around a JSON object. */
function extractJson(text: string): string {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidate = (fenced ? fenced[1] : text).trim();
  const start = candidate.indexOf('{');
  const end = candidate.lastIndexOf('}');
  if (start !== -1 && end !== -1 && end > start) return candidate.slice(start, end + 1);
  return candidate;
}

export async function runAI<T = string>(args: RunAIArgs<T>): Promise<RunAIResult<T>> {
  const model = MODEL_ROUTING[args.capability];
  const maxTokens = args.maxTokens ?? MAX_TOKENS[args.capability];
  const transport = getAiTransport();
  const startedAt = Date.now();

  const userMessage = {
    role: 'user' as const,
    content: typeof args.userContent === 'string' ? args.userContent : args.userContent,
  };

  const baseReq = {
    model,
    max_tokens: maxTokens,
    temperature: args.temperature,
    // Prompt caching on the static system prefix (spec §6.1).
    system: [{ type: 'text' as const, text: args.system, cache_control: { type: 'ephemeral' as const } }],
    messages: [userMessage],
  };

  let ok = true;
  let text = '';
  let usage = { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 };
  let returnedModel = model;
  let parsed: T | undefined;
  let repaired = false;

  try {
    const res = await transport.create(baseReq);
    text = res.text;
    usage = {
      input_tokens: res.usage.input_tokens,
      output_tokens: res.usage.output_tokens,
      cache_read_input_tokens: res.usage.cache_read_input_tokens ?? 0,
      cache_creation_input_tokens: res.usage.cache_creation_input_tokens ?? 0,
    };
    returnedModel = res.model;

    if (args.schema) {
      const first = args.schema.safeParse(safeJsonParse(extractJson(text)));
      if (first.success) {
        parsed = first.data;
      } else {
        // One repair retry: feed the model its bad output + the validation error.
        repaired = true;
        const repair = await transport.create({
          ...baseReq,
          messages: [
            userMessage,
            { role: 'assistant', content: text },
            {
              role: 'user',
              content:
                'Your previous response did not match the required JSON schema. ' +
                'Respond again with ONLY the corrected JSON object, no prose, no code fences. ' +
                `Validation error: ${first.error.issues.map((i) => i.message).join('; ')}`,
            },
          ],
        });
        text = repair.text;
        usage.input_tokens += repair.usage.input_tokens;
        usage.output_tokens += repair.usage.output_tokens;
        usage.cache_read_input_tokens += repair.usage.cache_read_input_tokens ?? 0;
        usage.cache_creation_input_tokens += repair.usage.cache_creation_input_tokens ?? 0;
        const second = args.schema.safeParse(safeJsonParse(extractJson(text)));
        if (second.success) parsed = second.data;
        else throw new AiParseError(first.error.message);
      }
    } else {
      parsed = text as unknown as T;
    }
  } catch (err) {
    ok = false;
    await logRun(args, returnedModel, usage, startedAt, ok, { error: String(err), repaired });
    throw err;
  }

  const costCents = await logRun(args, returnedModel, usage, startedAt, ok, { repaired });
  return { data: parsed as T, text, costCents, model: returnedModel };
}

function safeJsonParse(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

async function logRun(
  args: RunAIArgs<unknown>,
  model: string,
  usage: { input_tokens: number; output_tokens: number; cache_read_input_tokens: number; cache_creation_input_tokens: number },
  startedAt: number,
  ok: boolean,
  meta: Record<string, unknown>,
): Promise<number> {
  const costCents = computeCostCents(model, {
    inputTokens: usage.input_tokens,
    outputTokens: usage.output_tokens,
    cacheReadTokens: usage.cache_read_input_tokens,
    cacheCreationTokens: usage.cache_creation_input_tokens,
  });
  try {
    await db.insert(aiRuns).values({
      orgId: args.orgId,
      capability: args.capability,
      model,
      inputTokens: usage.input_tokens,
      outputTokens: usage.output_tokens,
      cachedInputTokens: usage.cache_read_input_tokens,
      costCents: costCents.toFixed(4),
      latencyMs: Date.now() - startedAt,
      inputRef: { kind: typeof args.userContent === 'string' ? 'text' : 'multimodal', ...meta },
      outputRef: null,
      ok,
    });
  } catch (err) {
    // Logging must never break the feature path.
    console.error('ai_runs log failed:', err);
  }
  return costCents;
}

export class AiParseError extends Error {
  constructor(message: string) {
    super(`AI returned invalid JSON after one repair attempt: ${message}`);
    this.name = 'AiParseError';
  }
}
