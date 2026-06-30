import { notFound } from 'next/navigation';
import { DateTime } from 'luxon';
import { requireCrew } from '@/lib/auth/context';
import { getShiftDetail } from '@/lib/domain/shifts';
import { getOrgTimezone } from '@/lib/domain/org';
import { CrewShiftScreen } from '@/components/crew/shift-screen';

export const dynamic = 'force-dynamic';

export default async function CrewShiftPage({ params }: { params: Promise<{ id: string }> }) {
  const auth = await requireCrew();
  const { id } = await params;
  const detail = await getShiftDetail(auth.orgId, id);
  if (!detail) notFound();

  // A cleaner may only open their own assigned shift.
  if (detail.shift.assignedUserId !== auth.userId) notFound();

  const tz = await getOrgTimezone(auth.orgId);
  const startLabel = DateTime.fromJSDate(detail.shift.scheduledStart, { zone: tz }).toFormat(
    'cccc, LLL d · h:mm a',
  );

  return (
    <CrewShiftScreen
      detail={{
        shiftId: detail.shift.id,
        siteId: detail.site.id,
        siteName: detail.site.name,
        address: detail.site.address,
        status: detail.shift.status,
        startLabel,
        clockInWithinGeofence: detail.shift.clockInWithinGeofence,
        results: detail.results.map((r) => ({
          id: r.id,
          labelSnapshot: r.labelSnapshot,
          completed: r.completed,
          photoId: r.photoId,
        })),
        requiresPhotoLabels: [],
        hasPhotos: detail.photos.length > 0,
      }}
    />
  );
}
