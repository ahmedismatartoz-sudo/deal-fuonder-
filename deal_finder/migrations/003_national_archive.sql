-- Nationwide raw catalogue: incomplete originals do not become verified analyses.
CREATE TABLE collection_pages (
    source text NOT NULL, run_id text NOT NULL, page_id text NOT NULL,
    checksum text NOT NULL, mode text NOT NULL CHECK (mode IN ('initial','incremental')),
    scope jsonb NOT NULL, received_at timestamptz NOT NULL, next_cursor text,
    complete boolean NOT NULL, accepted integer NOT NULL CHECK (accepted >= 0),
    rejected integer NOT NULL CHECK (rejected >= 0), sequence integer NOT NULL CHECK (sequence > 0),
    PRIMARY KEY(source, run_id, page_id), UNIQUE(source, run_id, sequence),
    CHECK (complete = (next_cursor IS NULL))
);
CREATE TABLE listing_events (
    source text NOT NULL, source_id text NOT NULL, observed_at timestamptz NOT NULL,
    url text NOT NULL, active boolean NOT NULL, country text NOT NULL CHECK (country='IT'),
    city text, province text, price_eur integer CHECK (price_eur > 0),
    price_kind text NOT NULL CHECK (price_kind IN ('total','installment','deposit','unknown')),
    latitude double precision CHECK (latitude BETWEEN -90 AND 90),
    longitude double precision CHECK (longitude BETWEEN -180 AND 180), payload jsonb NOT NULL,
    PRIMARY KEY(source, source_id, observed_at),
    CHECK ((latitude IS NULL) = (longitude IS NULL))
);
CREATE TABLE collection_quarantine (
    source text NOT NULL, run_id text NOT NULL, page_id text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0), payload jsonb NOT NULL, issue text NOT NULL,
    PRIMARY KEY(source, run_id, page_id, ordinal),
    FOREIGN KEY(source, run_id, page_id) REFERENCES collection_pages(source, run_id, page_id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX listing_events_latest ON listing_events(source, source_id, observed_at DESC);
CREATE INDEX listing_events_search ON listing_events(country, active, price_kind, price_eur);
CREATE INDEX listing_events_location ON listing_events(city, province, latitude);
CREATE INDEX snapshots_national_cohort ON snapshots (
    (payload->>'make'), (payload->>'model'), (payload->>'generation'),
    (payload->>'trim'), (payload->>'fuel'), (payload->>'transmission'), (payload->>'seller_type')
);
DO $$
DECLARE table_name text; client_role text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['collection_pages','listing_events','collection_quarantine']
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
