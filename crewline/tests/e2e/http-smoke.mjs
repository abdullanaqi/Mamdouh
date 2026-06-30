/**
 * End-to-end HTTP smoke test against a running Crewline server (next start).
 * Verifies the real auth-cookie + API-route + DB path for the core loop:
 *   owner sees dashboard → crew clocks in (geofence) → checklist → clock out
 *   → owner verification view shows the completed, verified shift.
 *
 * Usage: BASE_URL=http://localhost:3100 node tests/e2e/http-smoke.mjs
 */
import 'dotenv/config';
import postgres from 'postgres';
import { SignJWT } from 'jose';

const BASE = process.env.BASE_URL ?? 'http://localhost:3100';
const DB = process.env.DATABASE_URL;
const SECRET = new TextEncoder().encode(process.env.AUTH_SECRET);

let failures = 0;
function check(name, cond, extra = '') {
  if (cond) {
    console.log(`  ✓ ${name}`);
  } else {
    failures += 1;
    console.error(`  ✗ ${name} ${extra}`);
  }
}

async function cookieFor(userId, orgId, role) {
  const token = await new SignJWT({ userId, orgId, role })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setExpirationTime('1h')
    .sign(SECRET);
  return `crewline_session=${token}`;
}

async function main() {
  const sql = postgres(DB, { max: 1, prepare: false });

  const [org] = await sql`select id, timezone from orgs order by created_at limit 1`;
  const [owner] = await sql`
    select u.id from users u join org_users ou on ou.user_id = u.id
    where ou.org_id = ${org.id} and ou.role = 'owner' limit 1`;
  const [cleaner] = await sql`
    select u.id from users u join org_users ou on ou.user_id = u.id
    where ou.org_id = ${org.id} and ou.role = 'cleaner' limit 1`;
  const [site] = await sql`select id, lat, lng from sites where org_id = ${org.id} limit 1`;
  // A scheduled shift assigned to the cleaner.
  const [shift] = await sql`
    select id from shifts
    where org_id = ${org.id} and assigned_user_id = ${cleaner.id} and status = 'scheduled'
    order by scheduled_start limit 1`;

  if (!org || !owner || !cleaner || !site || !shift) {
    console.error('Seed data missing; run pnpm db:seed first.');
    process.exit(1);
  }

  const ownerCookie = await cookieFor(owner.id, org.id, 'owner');
  const crewCookie = await cookieFor(cleaner.id, org.id, 'cleaner');

  console.log('1) Owner dashboard');
  const dash = await fetch(`${BASE}/dashboard`, { headers: { cookie: ownerCookie } });
  const dashHtml = await dash.text();
  check('GET /dashboard 200', dash.status === 200, `status=${dash.status}`);
  check('dashboard renders heading', dashHtml.includes('Dashboard'));

  console.log('2) Crew clock-in (inside geofence)');
  const ci = await fetch(`${BASE}/api/crew/clock-in`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', cookie: crewCookie },
    body: JSON.stringify({ shiftId: shift.id, lat: site.lat, lng: site.lng, clientTime: Date.now() }),
  });
  const ciJson = await ci.json();
  check('clock-in 200', ci.status === 200, `status=${ci.status}`);
  check('clock-in within geofence', ciJson?.result?.withinGeofence === true, JSON.stringify(ciJson));

  console.log('3) Unauthorized clock-in is rejected');
  const noAuth = await fetch(`${BASE}/api/crew/clock-in`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ shiftId: shift.id, lat: site.lat, lng: site.lng }),
  });
  check('clock-in without cookie 401', noAuth.status === 401, `status=${noAuth.status}`);

  console.log('4) Complete checklist items');
  const results = await sql`
    select id from shift_checklist_results where shift_id = ${shift.id} order by id`;
  check('checklist snapshot created on clock-in', results.length > 0, `count=${results.length}`);
  for (const r of results) {
    const res = await fetch(`${BASE}/api/crew/checklist`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', cookie: crewCookie },
      body: JSON.stringify({ shiftId: shift.id, resultId: r.id, completed: true, clientTime: Date.now() }),
    });
    if (res.status !== 200) check(`checklist ${r.id}`, false, `status=${res.status}`);
  }

  console.log('5) Crew clock-out');
  const co = await fetch(`${BASE}/api/crew/clock-out`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', cookie: crewCookie },
    body: JSON.stringify({ shiftId: shift.id, clientTime: Date.now() }),
  });
  check('clock-out 200', co.status === 200, `status=${co.status}`);

  console.log('6) Owner verification view shows completed + 100% checklist');
  const ver = await fetch(`${BASE}/shifts/${shift.id}`, { headers: { cookie: ownerCookie } });
  // React separates adjacent text nodes with <!-- --> markers; strip them so
  // substring checks see the human-visible text (e.g. "100%", not "100<!---->%").
  const verHtml = (await ver.text()).replace(/<!--[^>]*-->/g, '');
  check('GET /shifts/[id] 200', ver.status === 200, `status=${ver.status}`);
  check('verification shows Completed', verHtml.includes('Completed'));
  check('verification shows 100%', verHtml.includes('100%'));
  check('verification shows On-site/Verified geofence', /Verified on-site/.test(verHtml));

  console.log('7) Tenancy: a foreign-org cookie cannot read this shift');
  const otherOrgCookie = await cookieFor(owner.id, '00000000-0000-0000-0000-000000000000', 'owner');
  const foreign = await fetch(`${BASE}/shifts/${shift.id}`, {
    headers: { cookie: otherOrgCookie },
    redirect: 'manual',
  });
  const foreignHtml = foreign.status >= 300 && foreign.status < 400 ? '' : await foreign.text();
  // getAuth re-checks membership against the DB; a bogus org → no membership →
  // requireOwner redirects to /login. The site name must never leak.
  check('foreign org does not see the shift site name', !foreignHtml.includes('North Office'));

  console.log('8) Cron routes: secret required + jobs run');
  const cronSecret = process.env.CRON_SECRET;
  const noSecret = await fetch(`${BASE}/api/cron/mark-missed`, { method: 'POST' });
  check('cron without secret 401', noSecret.status === 401, `status=${noSecret.status}`);

  for (const path of ['materialize', 'mark-missed', 'invoice-reminders']) {
    const r = await fetch(`${BASE}/api/cron/${path}`, {
      method: 'POST',
      headers: { 'x-cron-secret': cronSecret ?? '' },
    });
    const j = await r.json().catch(() => ({}));
    check(`cron ${path} 200 with secret`, r.status === 200 && j.ok === true, `status=${r.status} ${JSON.stringify(j)}`);
  }

  await sql.end();
  console.log(failures === 0 ? '\nALL E2E CHECKS PASSED' : `\n${failures} E2E CHECK(S) FAILED`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error('E2E error:', err);
  process.exit(1);
});
