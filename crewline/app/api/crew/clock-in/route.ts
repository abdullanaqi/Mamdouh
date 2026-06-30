import { handleCrew, clientTimeToDate } from '@/lib/crew/api';
import { clockIn } from '@/lib/domain/shifts';

export async function POST(req: Request) {
  return handleCrew(req, async (body, auth) => {
    const { shiftId, lat, lng, clientTime } = body;
    return clockIn(
      auth.orgId,
      String(shiftId),
      auth.userId,
      Number(lat),
      Number(lng),
      clientTimeToDate(clientTime),
    );
  });
}
