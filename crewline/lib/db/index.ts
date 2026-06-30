import { drizzle } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';
import * as schema from './schema';

/**
 * Postgres connection + Drizzle client.
 *
 * The app connects with a single role. Tenancy is enforced primarily in the
 * data-access layer (lib/db/dal.ts — every query is scoped by org_id) and,
 * as defense-in-depth, by Postgres Row-Level Security policies (lib/db/rls.sql)
 * keyed on the `app.current_org_id` session GUC. See README "Multi-tenancy".
 */
const connectionString = process.env.DATABASE_URL;

if (!connectionString) {
  throw new Error(
    'DATABASE_URL is not set. Copy .env.example to .env and set it (see README).',
  );
}

// Reuse a single client across hot reloads in dev.
const globalForDb = globalThis as unknown as { __crewlineSql?: ReturnType<typeof postgres> };

export const sql =
  globalForDb.__crewlineSql ??
  postgres(connectionString, {
    max: 10,
    prepare: false,
  });

if (process.env.NODE_ENV !== 'production') {
  globalForDb.__crewlineSql = sql;
}

export const db = drizzle(sql, { schema });

export { schema };
export type Database = typeof db;
