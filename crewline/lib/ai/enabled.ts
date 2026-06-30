/**
 * AI features require a real ANTHROPIC_API_KEY (a HANDBACK credential). When it
 * is absent, the UI shows a clear "add your key" state instead of erroring, and
 * best-effort enrichments (issue triage, photo checks) are skipped silently.
 * The AI wrapper itself is fully tested with an injected fake transport.
 */
export function aiEnabled(): boolean {
  return Boolean(process.env.ANTHROPIC_API_KEY);
}
