-- Least-privilege PostgreSQL role for the FusionPBX IVR Manager.
--
-- The app touches only a handful of FusionPBX tables. Create a dedicated role
-- with exactly the rights it needs — do NOT reuse the 'fusionpbx' superuser/owner
-- role. Run as a Postgres superuser against the FusionPBX database.
--
--   psql -U postgres -d fusionpbx -f sql/least_privilege_role.sql
--
-- Then set FPBX_DB_USER=ivr_manager / FPBX_DB_PASSWORD=... in the app env.

\set app_role ivr_manager

-- 1. Role: login only, no CREATEDB/CREATEROLE/SUPERUSER.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ivr_manager') THEN
    CREATE ROLE ivr_manager LOGIN PASSWORD 'CHANGE_ME_STRONG';
  END IF;
END $$;

-- 2. Connect + schema usage.
GRANT CONNECT ON DATABASE fusionpbx TO ivr_manager;
GRANT USAGE ON SCHEMA public TO ivr_manager;

-- 3. Table privileges — only what the app reads/writes.
--    Read-only reference data:
GRANT SELECT                         ON v_domains    TO ivr_manager;
GRANT SELECT                         ON v_extensions TO ivr_manager;
--    Managed schedule dialplans (create/replace/remove):
GRANT SELECT, INSERT, UPDATE, DELETE ON v_dialplans  TO ivr_manager;
--    Greeting recordings (create/replace):
GRANT SELECT, INSERT, UPDATE         ON v_recordings TO ivr_manager;

-- 4. Explicitly deny broad defaults: no privileges on any other table are
--    granted, and the role owns nothing. Revoke the public CREATE on schema so
--    it cannot add objects.
REVOKE CREATE ON SCHEMA public FROM ivr_manager;

-- Note: the app generates UUID text primary keys itself, so no sequence
-- privileges are required. If your FusionPBX schema version uses triggers that
-- touch other tables on write, grant the minimal extra rights those require.
