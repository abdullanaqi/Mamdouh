import { authorizeCron } from '@/lib/jobs/cron-auth';
import { runInvoiceReminders } from '@/lib/jobs';

export const maxDuration = 60;

export async function POST(req: Request) {
  if (!authorizeCron(req)) return Response.json({ error: 'Unauthorized' }, { status: 401 });
  const sent = await runInvoiceReminders();
  return Response.json({ ok: true, sent });
}

export const GET = POST;
