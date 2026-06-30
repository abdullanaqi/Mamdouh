import { authorizeCron } from '@/lib/jobs/cron-auth';
import { runMaterializeAllOrgs } from '@/lib/jobs';

export async function POST(req: Request) {
  if (!authorizeCron(req)) return Response.json({ error: 'Unauthorized' }, { status: 401 });
  const created = await runMaterializeAllOrgs(14);
  return Response.json({ ok: true, created });
}
