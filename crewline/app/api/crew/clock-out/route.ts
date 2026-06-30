import { handleCrew, clientTimeToDate } from '@/lib/crew/api';
import { clockOut } from '@/lib/domain/shifts';

export async function POST(req: Request) {
  return handleCrew(req, async (body, auth) => {
    const { shiftId, clientTime } = body;
    return clockOut(auth.orgId, String(shiftId), auth.userId, clientTimeToDate(clientTime));
  });
}
