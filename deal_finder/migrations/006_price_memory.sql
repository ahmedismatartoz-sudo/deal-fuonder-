-- Compact immutable observations: asking-price knowledge, never certified values.
CREATE TABLE price_observations (
 source text NOT NULL, source_id text NOT NULL, observed_at timestamptz NOT NULL,
 active boolean NOT NULL, make text, model text, payload jsonb NOT NULL,
 PRIMARY KEY(source,source_id,observed_at),
 FOREIGN KEY(source,source_id,observed_at) REFERENCES listing_events(source,source_id,observed_at)
);
CREATE INDEX price_family ON price_observations(make,model,source,source_id,observed_at DESC);
CREATE TABLE price_test_reports (
 run_id text PRIMARY KEY, as_of timestamptz NOT NULL, payload jsonb NOT NULL
);
DO $$
DECLARE t text; r text;
BEGIN
 FOREACH t IN ARRAY ARRAY['price_observations','price_test_reports'] LOOP
  EXECUTE format('ALTER TABLE deal_finder.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('REVOKE ALL ON deal_finder.%I FROM PUBLIC',t);
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated') LOOP
   EXECUTE format('REVOKE ALL ON deal_finder.%I FROM %I',t,r);
  END LOOP;
  EXECUTE format('GRANT SELECT,INSERT ON deal_finder.%I TO deal_finder_backend',t);
  EXECUTE format('CREATE POLICY backend_select ON deal_finder.%I FOR SELECT TO deal_finder_backend USING (true)',t);
  EXECUTE format('CREATE POLICY backend_insert ON deal_finder.%I FOR INSERT TO deal_finder_backend WITH CHECK (true)',t);
 END LOOP;
END $$;
