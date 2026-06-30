import { handleCrew, clientTimeToDate } from '@/lib/crew/api';
import { setChecklistResult } from '@/lib/domain/shifts';

export async function POST(req: Request) {
  return handleCrew(req, async (body, auth) => {
    const { shiftId, resultId, completed, clientTime } = body;
    return setChecklistResult(
      auth.orgId,
      String(shiftId),
      auth.userId,
      String(resultId),
      Boolean(completed),
      clientTimeToDate(clientTime),
    );
  });
}
