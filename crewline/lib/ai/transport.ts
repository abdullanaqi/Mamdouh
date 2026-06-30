import Anthropic from '@anthropic-ai/sdk';

/**
 * Minimal transport contract over the Anthropic Messages API. Centralizing it
 * here lets tests inject a fake (no API key, no network) while production uses
 * the real SDK. Shapes mirror the SDK (verified against @anthropic-ai/sdk types).
 */
export type AiTextBlock = { type: 'text'; text: string; cache_control?: { type: 'ephemeral' } };
export type AiImageBlock = {
  type: 'image';
  source: { type: 'base64'; media_type: 'image/jpeg' | 'image/png' | 'image/gif' | 'image/webp'; data: string };
};
export type AiContentBlock = AiTextBlock | AiImageBlock;

export type AiRequest = {
  model: string;
  max_tokens: number;
  system: AiTextBlock[];
  messages: Array<{ role: 'user' | 'assistant'; content: AiContentBlock[] | string }>;
  temperature?: number;
};

export type AiResponse = {
  text: string;
  usage: {
    input_tokens: number;
    output_tokens: number;
    cache_read_input_tokens?: number | null;
    cache_creation_input_tokens?: number | null;
  };
  model: string;
  stop_reason: string | null;
};

export interface AiTransport {
  create(req: AiRequest): Promise<AiResponse>;
}

/** Real transport backed by the Anthropic SDK. */
export class AnthropicTransport implements AiTransport {
  private client: Anthropic;
  constructor(apiKey?: string) {
    const key = apiKey ?? process.env.ANTHROPIC_API_KEY;
    if (!key) {
      throw new Error(
        'ANTHROPIC_API_KEY is not set. AI features require it; tests inject a fake transport instead.',
      );
    }
    this.client = new Anthropic({ apiKey: key });
  }

  async create(req: AiRequest): Promise<AiResponse> {
    const msg = await this.client.messages.create({
      model: req.model,
      max_tokens: req.max_tokens,
      temperature: req.temperature,
      system: req.system,
      messages: req.messages as Anthropic.MessageParam[],
    });
    const text = msg.content
      .filter((b): b is Anthropic.TextBlock => b.type === 'text')
      .map((b) => b.text)
      .join('');
    return {
      text,
      usage: {
        input_tokens: msg.usage.input_tokens,
        output_tokens: msg.usage.output_tokens,
        cache_read_input_tokens: msg.usage.cache_read_input_tokens,
        cache_creation_input_tokens: msg.usage.cache_creation_input_tokens,
      },
      model: msg.model,
      stop_reason: msg.stop_reason,
    };
  }
}

// Allow tests / jobs to override the transport globally.
let override: AiTransport | null = null;
export function setAiTransport(t: AiTransport | null) {
  override = t;
}
export function getAiTransport(): AiTransport {
  if (override) return override;
  return new AnthropicTransport();
}
