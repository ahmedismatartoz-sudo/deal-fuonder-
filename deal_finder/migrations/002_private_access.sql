-- Private service data: deny public clients and prepare a scoped backend role.
-- NOLOGIN means this role contains permissions, never deployable credentials.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'deal_finder_backend') THEN
        CREATE ROLE deal_finder_backend NOLOGIN NOSUPERUSER NOCREATEDB
            NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END $$;

REVOKE ALL ON SCHEMA deal_finder FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA deal_finder FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA deal_finder FROM PUBLIC;

DO $$
DECLARE client_role text;
BEGIN
    FOR client_role IN
        SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')
    LOOP
        EXECUTE format('REVOKE ALL ON SCHEMA deal_finder FROM %I', client_role);
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA deal_finder FROM %I', client_role);
        EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA deal_finder FROM %I', client_role);
    END LOOP;
END $$;

GRANT USAGE ON SCHEMA deal_finder TO deal_finder_backend;
GRANT SELECT ON ALL TABLES IN SCHEMA deal_finder TO deal_finder_backend;
GRANT INSERT ON snapshots, batches, raw_records, identity_attestations,
    jobs, agent_runs, evaluation_reports TO deal_finder_backend;
GRANT UPDATE ON jobs TO deal_finder_backend;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA deal_finder TO deal_finder_backend;

DO $$
DECLARE table_name text;
BEGIN
    FOR table_name IN
        SELECT tablename FROM pg_tables WHERE schemaname = 'deal_finder'
    LOOP
        EXECUTE format('ALTER TABLE deal_finder.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('CREATE POLICY backend_select ON deal_finder.%I FOR SELECT TO deal_finder_backend USING (true)', table_name);
        IF table_name <> 'schema_migrations' THEN
            EXECUTE format('CREATE POLICY backend_insert ON deal_finder.%I FOR INSERT TO deal_finder_backend WITH CHECK (true)', table_name);
        END IF;
    END LOOP;
END $$;
CREATE POLICY backend_update_jobs ON jobs FOR UPDATE TO deal_finder_backend
    USING (true) WITH CHECK (true);
-- Owners run migrations; the service role has no DDL or delete permissions.
