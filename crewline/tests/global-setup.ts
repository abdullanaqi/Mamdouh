// Runs once before the suite: applies table DDL + RLS to the test database.
import { config } from 'dotenv';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { drizzle } from 'drizzle-orm/postgres-js';
import { migrate } from 'drizzle-orm/postgres-js/migrator';
import postgres from 'postgres';

export default async function globalSetup() {
  config({ path: '.env.test' });
  const url =
    process.env.DATABASE_URL ?? 'postgresql://postgres@127.0.0.1:5433/crewline_test';

  const client = postgres(url, { max: 1, prepare: false });
  const db = drizzle(client);
  await migrate(db, { migrationsFolder: join(process.cwd(), 'lib/db/migrations') });
  const rls = readFileSync(join(process.cwd(), 'lib/db/rls.sql'), 'utf8');
  await client.unsafe(rls);
  await client.end();
}
