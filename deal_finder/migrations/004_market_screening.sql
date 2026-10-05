-- Immutable, indexed projection and auditable asking-price candidate reviews.
CREATE TABLE market_observations (
    source text NOT NULL, source_id text NOT NULL, observed_at timestamptz NOT NULL,
    signature text NOT NULL, payload jsonb, quality_issue text,
    make text, model text, generation text, trim text, fuel text,
    transmission text, seller_type text, year integer, mileage_km integer,
    PRIMARY KEY(source, source_id, observed_at),
    FOREIGN KEY(source, source_id, observed_at) REFERENCES listing_events(source, source_id, observed_at),
    CHECK ((payload IS NULL) = (quality_issue IS NOT NULL))
);
CREATE INDEX market_cohort ON market_observations
    (source, make, model, generation, trim, fuel, transmission, seller_type, year, mileage_km);
CREATE TABLE market_reviews (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source text NOT NULL, source_id text NOT NULL, observed_at timestamptz NOT NULL,
    version text NOT NULL, as_of timestamptz NOT NULL, signature text NOT NULL, basis_signature text NOT NULL,
    selected boolean NOT NULL, discount_fraction double precision, payload jsonb NOT NULL,
    UNIQUE(source, source_id, observed_at, version, as_of, basis_signature),
    FOREIGN KEY(source, source_id, observed_at) REFERENCES market_observations(source, source_id, observed_at)
);
CREATE INDEX market_reviews_latest ON market_reviews(source, source_id, version, as_of DESC);
DO $$
DECLARE table_name text; client_role text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['market_observations','market_reviews']
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
REVOKE ALL ON SEQUENCE market_reviews_id_seq FROM PUBLIC;
DO $$
DECLARE client_role text;
BEGIN
    FOR client_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')
    LOOP
        EXECUTE format('REVOKE ALL ON SEQUENCE deal_finder.market_reviews_id_seq FROM %I', client_role);
    END LOOP;
END $$;
GRANT USAGE ON SEQUENCE market_reviews_id_seq TO deal_finder_backend;
