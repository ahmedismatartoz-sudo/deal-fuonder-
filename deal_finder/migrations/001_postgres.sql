-- Private application schema; not exposed through Supabase's public Data API.
CREATE TABLE snapshots (
    source text NOT NULL,
    source_id text NOT NULL,
    observed_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY(source, source_id, observed_at)
);
CREATE TABLE batches (
    batch_id text PRIMARY KEY,
    checksum text NOT NULL,
    received_at timestamptz NOT NULL,
    record_count integer NOT NULL CHECK (record_count > 0)
);
CREATE TABLE raw_records (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id text NOT NULL REFERENCES batches(batch_id),
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
    PRIMARY KEY(source, source_id, observed_at),
    FOREIGN KEY(source, source_id, observed_at) REFERENCES snapshots(source, source_id, observed_at)
);
CREATE TABLE jobs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raw_id bigint NOT NULL REFERENCES raw_records(id),
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','running','done','dead')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL,
    lease_token text,
    lease_until timestamptz,
    last_error text,
    created_at timestamptz NOT NULL,
    CHECK ((state='running') = (lease_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE TABLE agent_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id bigint NOT NULL REFERENCES jobs(id),
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
CREATE INDEX snapshots_cohort ON snapshots (
    (payload->>'make'), (payload->>'model'), (payload->>'generation'),
    (payload->>'trim'), (payload->>'fuel'), (payload->>'transmission'),
    (payload->>'province'), (payload->>'seller_type')
);
CREATE INDEX snapshots_vehicle ON snapshots ((payload->>'vehicle_id'));
CREATE INDEX jobs_ready ON jobs(state, available_at, id);
CREATE INDEX jobs_raw ON jobs(raw_id);
CREATE INDEX raw_records_batch ON raw_records(batch_id, ordinal);
-- No public/anonymous access; trusted backend connections own these objects.
REVOKE ALL ON ALL TABLES IN SCHEMA deal_finder FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA deal_finder FROM PUBLIC;
