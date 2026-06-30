/**
 * Seed demo data (spec T0.2): one org, owner, cleaner, client, site, checklist,
 * and a week of shifts (past completed/missed + upcoming) so the dashboard and
 * verification views show live numbers.
 *
 * Usage: pnpm db:seed  (truncates first, then inserts — idempotent to re-run).
 */
import 'dotenv/config';
import postgres from 'postgres';
import { drizzle } from 'drizzle-orm/postgres-js';
import * as schema from './schema';
import { hashPassword } from '@/lib/auth/password';
import { resetDb } from './reset';

export const DEMO = {
  ownerEmail: 'owner@demo.crewline.app',
  ownerPassword: 'demo1234',
  cleanerPhone: '+15555550123',
  orgName: 'Sparkle Pro Cleaning',
};

function at(daysFromToday: number, hour: number, minute = 0): Date {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + daysFromToday);
  d.setHours(hour, minute, 0, 0);
  return d;
}

export async function seed(url = process.env.DATABASE_URL): Promise<{ orgId: string }> {
  if (!url) throw new Error('DATABASE_URL is required to seed.');
  await resetDb(url);

  const sql = postgres(url, { max: 1, prepare: false });
  const db = drizzle(sql, { schema });

  try {
    // --- org ---
    const [org] = await db
      .insert(schema.orgs)
      .values({ name: DEMO.orgName, timezone: 'America/Chicago', plan: 'pro' })
      .returning();

    // --- users ---
    const [owner] = await db
      .insert(schema.users)
      .values({
        email: DEMO.ownerEmail,
        fullName: 'Dana Owner',
        passwordHash: hashPassword(DEMO.ownerPassword),
      })
      .returning();

    const [cleaner] = await db
      .insert(schema.users)
      .values({ phone: DEMO.cleanerPhone, fullName: 'Cory Cleaner' })
      .returning();

    await db.insert(schema.orgUsers).values([
      { orgId: org.id, userId: owner.id, role: 'owner', status: 'active' },
      {
        orgId: org.id,
        userId: cleaner.id,
        role: 'cleaner',
        status: 'active',
        hourlyRateCents: 2200,
      },
    ]);

    // --- client + site ---
    const [client] = await db
      .insert(schema.clients)
      .values({
        orgId: org.id,
        name: 'Acme Dental Group',
        contactName: 'Pat Office Manager',
        contactEmail: 'office@acmedental.example',
        contactPhone: '+15555551000',
        billingTerms: 'net30',
        notes: 'Two operatories + waiting room. HIPAA-sensitive — verify trash + disinfection.',
      })
      .returning();

    const [site] = await db
      .insert(schema.sites)
      .values({
        orgId: org.id,
        clientId: client.id,
        name: 'Acme Dental — North Office',
        address: '233 S Wacker Dr, Chicago, IL 60606',
        lat: 41.8789,
        lng: -87.6359,
        geofenceRadiusM: 150,
        siteType: 'medical',
        squareFootage: 6000,
        serviceFrequency: 'weekly',
        contractRateCents: 85000,
        active: true,
      })
      .returning();

    const checklistRows = await db
      .insert(schema.siteChecklistItems)
      .values([
        { orgId: org.id, siteId: site.id, label: 'Empty all trash bins', area: 'All areas', requiresPhoto: true, sortOrder: 0 },
        { orgId: org.id, siteId: site.id, label: 'Clean and disinfect restroom sinks', area: 'Restrooms', requiresPhoto: true, sortOrder: 1 },
        { orgId: org.id, siteId: site.id, label: 'Mop hard floors', area: 'Lobby', requiresPhoto: false, sortOrder: 2 },
        { orgId: org.id, siteId: site.id, label: 'Disinfect operatory surfaces', area: 'Operatories', requiresPhoto: true, sortOrder: 3 },
        { orgId: org.id, siteId: site.id, label: 'Restock paper towels & soap', area: 'Restrooms', requiresPhoto: false, sortOrder: 4 },
      ])
      .returning();

    // --- recurrence (weekly, Mon/Wed/Fri evenings) ---
    const [recurrence] = await db
      .insert(schema.recurrences)
      .values({
        orgId: org.id,
        siteId: site.id,
        rrule: 'FREQ=WEEKLY;BYDAY=MO,WE,FR',
        defaultAssignedUserId: cleaner.id,
        startTimeLocal: '18:00',
        durationMinutes: 120,
        active: true,
      })
      .returning();

    // --- a week of shifts ---
    // Two completed (past), one missed (past), today + upcoming scheduled.
    const completedDays = [-4, -2];
    const missedDay = -6;
    const upcomingDays = [0, 1, 3];

    for (const d of completedDays) {
      const start = at(d, 18);
      const end = at(d, 20);
      const [shift] = await db
        .insert(schema.shifts)
        .values({
          orgId: org.id,
          siteId: site.id,
          assignedUserId: cleaner.id,
          scheduledStart: start,
          scheduledEnd: end,
          status: 'completed',
          clockInAt: new Date(start.getTime() + 3 * 60_000),
          clockInLat: 41.879,
          clockInLng: -87.6361,
          clockInWithinGeofence: true,
          clockOutAt: new Date(end.getTime() - 5 * 60_000),
          recurrenceId: recurrence.id,
          aiQualityScore: 92,
          aiQualityNotes: 'All photo tasks documented; restroom and trash verified.',
        })
        .returning();

      await db.insert(schema.shiftChecklistResults).values(
        checklistRows.map((c) => ({
          orgId: org.id,
          shiftId: shift.id,
          checklistItemId: c.id,
          labelSnapshot: c.label,
          completed: true,
          completedAt: new Date(start.getTime() + 45 * 60_000),
        })),
      );

      await db.insert(schema.shiftPhotos).values([
        {
          orgId: org.id,
          shiftId: shift.id,
          storageKey: `${org.id}/demo-trash.jpg`,
          url: '/demo/trash.jpg',
          caption: 'Trash emptied, liners replaced',
          aiPass: true,
          aiReason: 'Bin is empty with a fresh liner.',
        },
        {
          orgId: org.id,
          shiftId: shift.id,
          storageKey: `${org.id}/demo-restroom.jpg`,
          url: '/demo/restroom.jpg',
          caption: 'Restroom sinks disinfected',
          aiPass: true,
          aiReason: 'Sink and counter appear clean and dry.',
        },
      ]);
    }

    // missed
    await db.insert(schema.shifts).values({
      orgId: org.id,
      siteId: site.id,
      assignedUserId: cleaner.id,
      scheduledStart: at(missedDay, 18),
      scheduledEnd: at(missedDay, 20),
      status: 'missed',
      recurrenceId: recurrence.id,
    });

    // upcoming
    for (const d of upcomingDays) {
      await db.insert(schema.shifts).values({
        orgId: org.id,
        siteId: site.id,
        assignedUserId: cleaner.id,
        scheduledStart: at(d, 18),
        scheduledEnd: at(d, 20),
        status: 'scheduled',
        recurrenceId: recurrence.id,
      });
    }

    // --- an open issue + a client message ---
    await db.insert(schema.issues).values({
      orgId: org.id,
      siteId: site.id,
      reportedByUserId: cleaner.id,
      source: 'crew',
      severity: 'medium',
      status: 'open',
      description: 'Soap dispenser in the west restroom is broken and needs replacing.',
      aiSummary: 'Broken soap dispenser (west restroom) needs replacement.',
    });

    await db.insert(schema.messages).values({
      orgId: org.id,
      clientId: client.id,
      siteId: site.id,
      direction: 'inbound',
      channel: 'email',
      body: 'Hi — the team did a great job last week, but can you make sure the break room trash gets emptied too?',
      aiGenerated: false,
      status: 'received',
    });

    console.log('✓ seeded demo org:', org.name, `(${org.id})`);
    console.log(`  owner login: ${DEMO.ownerEmail} / ${DEMO.ownerPassword}`);
    console.log(`  cleaner login: phone ${DEMO.cleanerPhone} (OTP shown on dev login screen)`);
    return { orgId: org.id };
  } finally {
    await sql.end();
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  seed()
    .then(() => process.exit(0))
    .catch((err) => {
      console.error('seed failed:', err);
      process.exit(1);
    });
}
