-- Historical design draft. Do NOT apply; use deal-finder migrate with packaged migrations.
-- PostgreSQL deployment contract; local runtime implements these tables in SQLite.
-- Apply after 001_initial.sql. A PostgreSQL worker adapter remains to be built.
CREATE TABLE ingestion_batches (
    batch_id text PRIMARY KEY,
    checksum text NOT NULL,
    received_at timestamptz NOT NULL,
    record_count integer NOT NULL CHECK (record_count > 0)
);
CREATE TABLE raw_records (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id text NOT NULL REFERENCES ingestion_batches(batch_id),
    ordinal integer NOT NULL,
    payload jsonb NOT NULL,
    quality_issue text,
    UNIQUE(batch_id, ordinal)
);
CREATE TABLE identity_attestations (
    source text NOT NULL,
    source_id text NOT NULL,
    observed_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY(source, source_id, observed_at)
);
CREATE TABLE agent_jobs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raw_id bigint NOT NULL REFERENCES raw_records(id),
    state text NOT NULL CHECK (state IN ('pending','running','done','dead')),
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL,
    lease_token text,
    lease_until timestamptz,
    last_error text,
    created_at timestamptz NOT NULL
);
CREATE INDEX agent_jobs_ready ON agent_jobs(state, available_at, id);
CREATE TABLE agent_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id bigint NOT NULL REFERENCES agent_jobs(id),
    attempt integer NOT NULL,
    as_of timestamptz NOT NULL,
    pipeline_version text NOT NULL,
    inputs jsonb NOT NULL,
    input_checksum text NOT NULL,
    outputs jsonb NOT NULL,
    finished_at timestamptz NOT NULL,
    UNIQUE(job_id, attempt)
);
CREATE TABLE evaluation_reports (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL,
    model_version text NOT NULL,
    inputs jsonb NOT NULL,
    outputs jsonb NOT NULL
);
