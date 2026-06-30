/**
 * Migration runner: applies Drizzle table DDL, then the RLS policy file.
 * Usage: pnpm db:migrate
 */
import 'dotenv/config';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { drizzle } from 'drizzle-orm/postgres-js';
import { migrate } from 'drizzle-orm/postgres-js/migrator';
import postgres from 'postgres';

const __dirname = dirname(fileURLToPath(import.meta.url));

async function main() {
  const url = process.env.DATABASE_URL;
  if (!url) throw new Error('DATABASE_URL is required to migrate.');

  // A dedicated single-use connection (max:1) for migrations.
  const migrationClient = postgres(url, { max: 1, prepare: false });
  const db = drizzle(migrationClient);

  console.log('• applying table migrations…');
  await migrate(db, { migrationsFolder: join(__dirname, 'migrations') });

  console.log('• applying RLS policies…');
  const rls = readFileSync(join(__dirname, 'rls.sql'), 'utf8');
  await migrationClient.unsafe(rls);

  console.log('✓ migrations + RLS applied');
  await migrationClient.end();
}

main().catch((err) => {
  console.error('migration failed:', err);
  process.exit(1);
});
