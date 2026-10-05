-- Persistent reservations and immutable provider observations, never public.
CREATE TABLE plate_lookup_attempts (
    id text PRIMARY KEY, provider text NOT NULL, plate text NOT NULL,
    requested_at timestamptz NOT NULL
);
CREATE INDEX plate_lookup_budget ON plate_lookup_attempts(provider, requested_at);
CREATE TABLE plate_lookup_results (
    attempt_id text PRIMARY KEY REFERENCES plate_lookup_attempts(id),
    provider text NOT NULL, plate text NOT NULL, received_at timestamptz NOT NULL,
    payload jsonb NOT NULL
);
CREATE INDEX plate_lookup_cache ON plate_lookup_results(provider, plate, received_at DESC);
DO $$
DECLARE table_name text; client_role text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['plate_lookup_attempts','plate_lookup_results']
    LOOP
        EXECUTE format('ALTER TABLE deal_finder.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('REVOKE ALL ON deal_finder.%I FROM PUBLIC', table_name);
        FOR client_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')
        LOOP
            EXECUTE format('REVOKE ALL ON deal_finder.%I FROM %I', table_name, client_role);
        END LOOP;
        EXECUTE format('GRANT SELECT, INSERT ON deal_finder.%I TO deal_finder_backend', table_name);
        EXECUTE format('CREATE POLICY backend_select ON deal_finder.%I FOR SELECT TO deal_finder_backend USING (true)', table_name);
        EXECUTE format('CREATE POLICY backend_insert ON deal_finder.%I FOR INSERT TO deal_finder_backend WITH CHECK (true)', table_name);
    END LOOP;
END $$;
