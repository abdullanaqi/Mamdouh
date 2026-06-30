-- Crewline Row-Level Security (spec §5.3) — defense-in-depth for multi-tenancy.
--
-- The app's primary tenancy guarantee is the data-access layer (lib/db/dal.ts),
-- which scopes every query by org_id. These RLS policies are a second wall:
-- when the connection role is the non-superuser `crewline_app` and the session
-- GUC `app.current_org_id` is set, the database itself refuses cross-org rows.
--
-- Idempotent: safe to run on every migrate.
SET client_min_messages = warning;

-- 1. Restricted application role (cannot bypass RLS; owns nothing).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crewline_app') THEN
    CREATE ROLE crewline_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO crewline_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO crewline_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO crewline_app;

-- Helper: read the current org from the session GUC (NULL if unset).
CREATE OR REPLACE FUNCTION app_current_org_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.current_org_id', true), '')::uuid
$$;

-- 2. Tenant tables keyed on org_id.
DO $$
DECLARE
  t text;
  tenant_tables text[] := ARRAY[
    'org_users','clients','sites','site_checklist_items','recurrences',
    'shifts','shift_checklist_results','shift_photos','issues','quotes',
    'invoices','invoice_line_items','messages','ai_runs','stripe_accounts'
  ];
BEGIN
  FOREACH t IN ARRAY tenant_tables LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_org_isolation', t);
    EXECUTE format(
      'CREATE POLICY %I ON %I USING (org_id = app_current_org_id()) WITH CHECK (org_id = app_current_org_id())',
      t || '_org_isolation', t
    );
  END LOOP;
END
$$;

-- 3. orgs keyed on id (a member may only see their current org).
ALTER TABLE orgs ENABLE ROW LEVEL SECURITY;
ALTER TABLE orgs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS orgs_isolation ON orgs;
CREATE POLICY orgs_isolation ON orgs
  USING (id = app_current_org_id())
  WITH CHECK (id = app_current_org_id());

-- 4. users is a shared identity table (no org_id). It is reachable from the app
-- role for joins; tenancy on people is enforced through org_users. Leave RLS
-- off here intentionally (documented), so name/contact lookups for assigned
-- crew work. No client data lives on this table.
