-- Compact source facts are recovered once per immutable current observation.
CREATE TABLE identity_source_cache (
 version text NOT NULL, source text NOT NULL, source_id text NOT NULL,
 observed_at timestamptz NOT NULL, seller_type text, source_fields jsonb NOT NULL,
 PRIMARY KEY(version,source,source_id,observed_at),
 FOREIGN KEY(source,source_id,observed_at) REFERENCES listing_events(source,source_id,observed_at)
);
ALTER TABLE identity_source_cache ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON identity_source_cache FROM PUBLIC;
DO $$
DECLARE r text;
BEGIN
 FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated') LOOP
  EXECUTE format('REVOKE ALL ON deal_finder.identity_source_cache FROM %I',r);
 END LOOP;
END $$;
GRANT SELECT,INSERT ON identity_source_cache TO deal_finder_backend;
CREATE POLICY backend_select ON identity_source_cache FOR SELECT TO deal_finder_backend USING (true);
CREATE POLICY backend_insert ON identity_source_cache FOR INSERT TO deal_finder_backend WITH CHECK (true);
