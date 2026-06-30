import { handleCrew } from '@/lib/crew/api';
import { createIssue } from '@/lib/domain/issues';
import { triageIssueBestEffort } from '@/lib/ai/orchestration';

export async function POST(req: Request) {
  return handleCrew(req, async (body, auth) => {
    const { shiftId, siteId, description, severity } = body;
    const issue = await createIssue(auth.orgId, {
      shiftId: shiftId ? String(shiftId) : null,
      siteId: siteId ? String(siteId) : null,
      reportedByUserId: auth.userId,
      source: 'crew',
      severity: severity === 'low' || severity === 'high' ? severity : 'medium',
      description: String(description ?? ''),
    });
    // Best-effort AI triage (skipped silently if no API key).
    await triageIssueBestEffort(auth.orgId, issue.id, issue.description, undefined, 'crew');
    return issue;
  });
}
