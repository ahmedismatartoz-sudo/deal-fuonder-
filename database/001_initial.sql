-- Historical design draft. Do NOT apply; use deal-finder migrate with packaged migrations.
-- PostgreSQL target schema. The initial API uses SQLite for local development.
CREATE TABLE listing_snapshots (
    source text NOT NULL,
    source_id text NOT NULL,
    observed_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (source, source_id, observed_at)
);
CREATE INDEX listing_snapshots_latest ON listing_snapshots (source, source_id, observed_at DESC);
CREATE TABLE valuation_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    model_version text NOT NULL,
    target jsonb NOT NULL,
    result jsonb NOT NULL
);
