import 'server-only';
import { aiEnabled } from './enabled';
import { triageIssue } from './capabilities';
import { applyIssueTriage } from '@/lib/domain/issues';

/**
 * Best-effort AI enrichment that must never break the user flow. If no API key
 * is configured (the common dev/test case — HANDBACK), these are skipped
 * silently; on any AI error we log and move on, leaving the human-entered data.
 */
export async function triageIssueBestEffort(
  orgId: string,
  issueId: string,
  description: string,
  siteName: string | undefined,
  source: string,
): Promise<void> {
  if (!aiEnabled()) return;
  try {
    const out = await triageIssue(orgId, { description, siteName, source });
    await applyIssueTriage(orgId, issueId, { severity: out.severity, aiSummary: out.summary });
  } catch (err) {
    console.error('issue triage skipped:', err);
  }
}
