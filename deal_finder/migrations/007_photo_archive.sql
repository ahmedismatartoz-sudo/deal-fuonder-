-- Retained original photo bytes and immutable per-observation provenance.
CREATE TABLE photo_assets (
 sha text PRIMARY KEY CHECK (sha ~ '^[0-9a-f]{64}$'),
 mime text NOT NULL CHECK (mime IN ('image/jpeg','image/png','image/webp')),
 content bytea NOT NULL, byte_count integer NOT NULL CHECK (byte_count>0 AND byte_count<=2097152),
 saved_at timestamptz NOT NULL, CHECK (octet_length(content)=byte_count)
);
CREATE TABLE photo_references (
 source text NOT NULL, source_id text NOT NULL, observed_at timestamptz NOT NULL,
 ordinal integer NOT NULL CHECK (ordinal>=0), original_url text NOT NULL,
 attempt integer NOT NULL CHECK (attempt BETWEEN 1 AND 3), sha text REFERENCES photo_assets(sha),
 saved_at timestamptz NOT NULL, error_code text,
 PRIMARY KEY(source,source_id,observed_at,ordinal,attempt),
 FOREIGN KEY(source,source_id,observed_at) REFERENCES listing_events(source,source_id,observed_at),
 CHECK ((sha IS NULL)=(error_code IS NOT NULL))
);
CREATE INDEX photo_original_url ON photo_references(original_url, saved_at DESC) WHERE sha IS NOT NULL;
DO $$
DECLARE t text; r text;
BEGIN
 FOREACH t IN ARRAY ARRAY['photo_assets','photo_references'] LOOP
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
