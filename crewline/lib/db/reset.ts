/**
 * Truncate all application tables (keeps the schema). Usage: pnpm db:reset
 * Used before reseeding and by the test harness.
 */
import 'dotenv/config';
import postgres from 'postgres';

const TABLES = [
  'ai_runs',
  'messages',
  'invoice_line_items',
  'invoices',
  'quotes',
  'issues',
  'shift_photos',
  'shift_checklist_results',
  'shifts',
  'recurrences',
  'site_checklist_items',
  'sites',
  'clients',
  'stripe_accounts',
  'org_users',
  'orgs',
  'users',
];

export async function resetDb(url = process.env.DATABASE_URL): Promise<void> {
  if (!url) throw new Error('DATABASE_URL is required to reset.');
  const sql = postgres(url, { max: 1, prepare: false });
  try {
    await sql.unsafe(`TRUNCATE TABLE ${TABLES.map((t) => `"${t}"`).join(', ')} CASCADE`);
  } finally {
    await sql.end();
  }
}

// Allow running directly.
if (import.meta.url === `file://${process.argv[1]}`) {
  resetDb()
    .then(() => {
      console.log('✓ database truncated');
      process.exit(0);
    })
    .catch((err) => {
      console.error('reset failed:', err);
      process.exit(1);
    });
}
