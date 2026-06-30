import { Badge } from '@/components/ui/badge';

const MAP: Record<string, { label: string; variant: 'default' | 'secondary' | 'success' | 'warning' | 'destructive' | 'outline' }> = {
  scheduled: { label: 'Scheduled', variant: 'secondary' },
  in_progress: { label: 'In progress', variant: 'default' },
  completed: { label: 'Completed', variant: 'success' },
  missed: { label: 'Missed', variant: 'destructive' },
  canceled: { label: 'Canceled', variant: 'outline' },
};

export function ShiftStatusBadge({ status }: { status: string }) {
  const m = MAP[status] ?? { label: status, variant: 'outline' as const };
  return <Badge variant={m.variant}>{m.label}</Badge>;
}
