import { authorizeCron } from '@/lib/jobs/cron-auth';
import { runPhotoQualityScan } from '@/lib/jobs';

export const maxDuration = 60;

export async function POST(req: Request) {
  if (!authorizeCron(req)) return Response.json({ error: 'Unauthorized' }, { status: 401 });
  const reviewed = await runPhotoQualityScan();
  return Response.json({ ok: true, reviewed });
}
