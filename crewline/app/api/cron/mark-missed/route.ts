import { authorizeCron } from '@/lib/jobs/cron-auth';
import { runMarkMissedAllOrgs } from '@/lib/jobs';

export async function POST(req: Request) {
  if (!authorizeCron(req)) return Response.json({ error: 'Unauthorized' }, { status: 401 });
  const missed = await runMarkMissedAllOrgs();
  return Response.json({ ok: true, missed });
}
