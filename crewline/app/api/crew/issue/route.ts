import { handleCrew } from '@/lib/crew/api';
import { createIssue } from '@/lib/domain/issues';

export async function POST(req: Request) {
  return handleCrew(req, async (body, auth) => {
    const { shiftId, siteId, description, severity } = body;
    return createIssue(auth.orgId, {
      shiftId: shiftId ? String(shiftId) : null,
      siteId: siteId ? String(siteId) : null,
      reportedByUserId: auth.userId,
      source: 'crew',
      severity: severity === 'low' || severity === 'high' ? severity : 'medium',
      description: String(description ?? ''),
    });
  });
}
