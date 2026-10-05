"""Durable local job queue. SQLite supports multiple local workers with fenced leases."""
import hashlib
import json
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from .storage import Store
from .models import Listing
from .agents import analyze, PIPELINE_VERSION


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Queue(Store):
    def __init__(self, path='deal-finder.db'):
        super().__init__(path)
        self.db.execute('PRAGMA busy_timeout=10000')
        self.db.execute('PRAGMA journal_mode=WAL')
        cohort = ('make', 'model', 'generation', 'trim', 'fuel', 'transmission', 'province', 'seller_type')
        columns = ','.join(f"json_extract(payload, '$.{k}')" for k in cohort)
        self.db.execute(f'CREATE INDEX IF NOT EXISTS snapshots_cohort ON snapshots({columns})')
        self.db.execute("CREATE INDEX IF NOT EXISTS snapshots_vehicle ON snapshots(json_extract(payload, '$.vehicle_id'))")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT PRIMARY KEY, checksum TEXT NOT NULL, received_at TEXT NOT NULL,
            record_count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS raw_records (
            id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            payload TEXT NOT NULL, quality_issue TEXT,
            UNIQUE(batch_id, ordinal));
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY, raw_id INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            available_at TEXT NOT NULL, lease_token TEXT, lease_until TEXT,
            last_error TEXT, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state, available_at, id);
        CREATE TABLE IF NOT EXISTS agent_runs (
            id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, attempt INTEGER NOT NULL,
            as_of TEXT NOT NULL, pipeline_version TEXT NOT NULL,
            inputs TEXT NOT NULL, input_checksum TEXT NOT NULL, outputs TEXT NOT NULL,
            finished_at TEXT NOT NULL, UNIQUE(job_id, attempt));
        CREATE TABLE IF NOT EXISTS identity_attestations (
            source TEXT NOT NULL, source_id TEXT NOT NULL, observed_at TEXT NOT NULL,
            payload TEXT NOT NULL, PRIMARY KEY(source, source_id, observed_at));
        CREATE TABLE IF NOT EXISTS evaluation_reports (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
            model_version TEXT NOT NULL, inputs TEXT NOT NULL, outputs TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS raw_records_batch ON raw_records(batch_id, ordinal);
        ''')

    def submit(self, batch_id, records):
        if not isinstance(batch_id, str) or not batch_id.strip() or len(batch_id) > 200:
            raise ValueError('batch_id requires a nonempty string of at most 200 characters')
        if not isinstance(records, list) or not 1 <= len(records) <= 5000:
            raise ValueError('Batch requires between 1 and 5000 records')
        serialized = canonical(records)
        if len(serialized.encode()) > 20_000_000:
            raise ValueError('Batch exceeds 20 MB')
        checksum = hashlib.sha256(serialized.encode()).hexdigest()
        timestamp = now_iso()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            previous = self.db.execute('SELECT checksum FROM batches WHERE batch_id=?', (batch_id,)).fetchone()
            if previous:
                if previous[0] != checksum:
                    raise ValueError('batch_id already used with different content')
                self.db.commit()
                return dict(batch_id=batch_id, idempotent=True, **self.batch(batch_id))
            self.db.execute('INSERT INTO batches VALUES (?, ?, ?, ?)', (batch_id, checksum, timestamp, len(records)))
            # Normalize the entire batch before any worker can claim it, so early
            # jobs can use later comparable records from the same incoming batch.
            for ordinal, raw in enumerate(records):
                issue = None
                try:
                    if not isinstance(raw, dict):
                        raise ValueError('Record must be an object with a listing field')
                    allowed = {'listing', 'inspection', 'repair_quotes', 'operating_costs', 'identity_evidence'}
                    if set(raw) - allowed:
                        raise ValueError('Unknown envelope fields: ' + ', '.join(sorted(set(raw)-allowed)))
                    listing = Listing.parse(raw['listing'])
                    payload = json.dumps(listing.to_dict(), sort_keys=True)
                    prior = self.db.execute('SELECT payload FROM snapshots WHERE source=? AND source_id=? AND observed_at=?',
                                            (*listing.identity, listing.observed_at)).fetchone()
                    if prior and prior[0] != payload:
                        raise ValueError('Conflicting immutable listing snapshot')
                    proof = raw.get('identity_evidence')
                    if proof is not None:
                        encoded = canonical(proof)
                        old_proof = self.db.execute('SELECT payload FROM identity_attestations WHERE source=? AND source_id=? AND observed_at=?', (*listing.identity, listing.observed_at)).fetchone()
                        if old_proof and old_proof[0] != encoded:
                            raise ValueError('Conflicting immutable identity attestation')
                        if not old_proof:
                            self.db.execute('INSERT INTO identity_attestations VALUES (?, ?, ?, ?)', (*listing.identity, listing.observed_at, encoded))
                    if prior is None:
                        self.db.execute('INSERT INTO snapshots VALUES (?, ?, ?, ?)',
                                        (*listing.identity, listing.observed_at, payload))
                except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
                    issue = str(error)
                cursor = self.db.execute('INSERT INTO raw_records(batch_id, ordinal, payload, quality_issue) VALUES (?, ?, ?, ?)',
                                         (batch_id, ordinal, canonical(raw), issue))
                self.db.execute('INSERT INTO jobs(raw_id, available_at, created_at) VALUES (?, ?, ?)',
                                (cursor.lastrowid, timestamp, timestamp))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return dict(batch_id=batch_id, idempotent=False, **self.batch(batch_id))

    def batch(self, batch_id):
        found = self.db.execute('SELECT record_count FROM batches WHERE batch_id=?', (batch_id,)).fetchone()
        if found is None:
            raise KeyError('Batch not found')
        invalid = self.db.execute('SELECT COUNT(*) FROM raw_records WHERE batch_id=? AND quality_issue IS NOT NULL', (batch_id,)).fetchone()[0]
        states = dict(self.db.execute('SELECT j.state, COUNT(*) FROM jobs j JOIN raw_records r ON r.id=j.raw_id WHERE r.batch_id=? GROUP BY j.state', (batch_id,)))
        return dict(record_count=found[0], quarantined_at_intake=invalid, jobs=states)

    def claim(self, *, lease_seconds=300, max_attempts=3, at=None):
        if type(lease_seconds) is not int or lease_seconds < 1 or max_attempts < 1:
            raise ValueError('Invalid lease or attempt limit')
        moment = at or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            raise ValueError('Claim time requires timezone')
        timestamp = moment.astimezone(timezone.utc).isoformat()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("UPDATE jobs SET state='dead', last_error='Lease expired after final attempt', lease_token=NULL, lease_until=NULL WHERE state='running' AND lease_until<=? AND attempts>=?", (timestamp, max_attempts))
            self.db.execute("UPDATE jobs SET state='pending', lease_token=NULL, lease_until=NULL WHERE state='running' AND lease_until<=? AND attempts<?", (timestamp, max_attempts))
            job = self.db.execute("SELECT id, raw_id, attempts FROM jobs WHERE state='pending' AND available_at<=? AND attempts<? ORDER BY id LIMIT 1", (timestamp, max_attempts)).fetchone()
            if job is None:
                self.db.commit()
                return None
            token = uuid4().hex
            deadline = (moment + timedelta(seconds=lease_seconds)).astimezone(timezone.utc).isoformat()
            self.db.execute("UPDATE jobs SET state='running', attempts=attempts+1, lease_token=?, lease_until=? WHERE id=?", (token, deadline, job[0]))
            self.db.commit()
            raw = self.db.execute('SELECT payload, quality_issue FROM raw_records WHERE id=?', (job[1],)).fetchone()
            return dict(id=job[0], raw_id=job[1], attempt=job[2]+1, token=token,
                        raw=json.loads(raw[0]), quality_issue=raw[1])
        except Exception:
            self.db.rollback()
            raise

    def candidates(self, target, as_of):
        # Latest source observation is chosen BEFORE matching specs. This prevents
        # obsolete variants from remaining comparable after a seller correction.
        fields = ('make', 'model', 'generation', 'trim', 'fuel', 'transmission', 'province', 'seller_type')
        predicates = ' AND '.join(f"json_extract(s.payload, '$.{k}')=?" for k in fields)
        params = [getattr(target, k) for k in fields]
        if target.vehicle_id:
            predicates = '(' + predicates + " OR json_extract(s.payload, '$.vehicle_id')=?)"
            params.append(target.vehicle_id)
        params.extend([as_of.isoformat(), as_of.isoformat()])
        query = """SELECT s.payload FROM snapshots s WHERE """ + predicates + """
            AND s.observed_at<=? AND NOT EXISTS (
                SELECT 1 FROM snapshots newer WHERE newer.source=s.source
                AND newer.source_id=s.source_id AND newer.observed_at>s.observed_at
                AND newer.observed_at<=?) ORDER BY s.source, s.source_id"""
        return [Listing.parse(json.loads(row[0])) for row in self.db.execute(query, params)]

    def finish(self, job, as_of, candidates, outputs, *, at=None, identity_evidence=None):
        finished = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        inputs = canonical({'raw': job['raw'], 'intake_issue': job['quality_issue'],
                            'candidates': [x.to_dict() for x in candidates],
                            'identity_evidence': [dict(key=list(k), proof=v) for k,v in (identity_evidence or {}).items()]})
        checksum = hashlib.sha256(inputs.encode()).hexdigest()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            changed = self.db.execute("UPDATE jobs SET state='done', lease_token=NULL, lease_until=NULL WHERE id=? AND state='running' AND lease_token=? AND lease_until>?", (job['id'], job['token'], finished)).rowcount
            if not changed:
                raise ValueError('Lost or expired job lease; stale worker cannot publish results')
            self.db.execute('INSERT INTO agent_runs(job_id, attempt, as_of, pipeline_version, inputs, input_checksum, outputs, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                            (job['id'], job['attempt'], as_of.isoformat(), PIPELINE_VERSION, inputs, checksum, canonical(outputs), finished))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def fail(self, job, error, *, max_attempts=3):
        state = 'dead' if job['attempt'] >= max_attempts else 'pending'
        available = (datetime.now(timezone.utc)+timedelta(seconds=2 ** job['attempt'])).isoformat()
        with self.db:
            self.db.execute("UPDATE jobs SET state=?, available_at=?, last_error=?, lease_token=NULL, lease_until=NULL WHERE id=? AND state='running' AND lease_token=?", (state, available, str(error)[:2000], job['id'], job['token']))

    def work_one(self):
        job = self.claim()
        if job is None:
            return None
        try:
            as_of = datetime.now(timezone.utc)
            raw = dict(job['raw']) if isinstance(job['raw'], dict) else {'listing': job['raw']}
            if job['quality_issue']:
                raw['_intake_error'] = job['quality_issue']
            candidates = []
            if not job['quality_issue']:
                candidates = self.candidates(Listing.parse(raw['listing']), as_of)
            proofs = {}
            for item in candidates:
                key = (*item.identity, item.observed_at)
                row = self.db.execute('SELECT payload FROM identity_attestations WHERE source=? AND source_id=? AND observed_at=?', key).fetchone()
                if row:
                    proofs[key] = json.loads(row[0])
            outputs = analyze(raw, candidates, as_of, identity_evidence=proofs)
            self.finish(job, as_of, candidates, outputs, identity_evidence=proofs)
            return dict(job_id=job['id'], state='done', analysis=outputs['validation']['data'])
        except Exception as error:
            self.fail(job, error)
            return dict(job_id=job['id'], state='retry_or_dead', error=str(error))

    def result(self, job_id):
        job = self.db.execute('SELECT state, attempts, last_error FROM jobs WHERE id=?', (job_id,)).fetchone()
        if job is None:
            raise KeyError('Job not found')
        run = self.db.execute('SELECT id, as_of, pipeline_version, input_checksum, outputs FROM agent_runs WHERE job_id=? ORDER BY id DESC LIMIT 1', (job_id,)).fetchone()
        return dict(job_id=job_id, state=job[0], attempts=job[1], last_error=job[2],
                    run=None if run is None else dict(id=run[0], as_of=run[1], pipeline_version=run[2], input_checksum=run[3], outputs=json.loads(run[4])))

    def replay_batch(self, batch_id):
        self.batch(batch_id)
        timestamp = now_iso()
        with self.db:
            ids = [row[0] for row in self.db.execute('SELECT id FROM raw_records WHERE batch_id=? ORDER BY ordinal', (batch_id,))]
            for raw_id in ids:
                self.db.execute('INSERT INTO jobs(raw_id, available_at, created_at) VALUES (?, ?, ?)', (raw_id, timestamp, timestamp))
        return {'batch_id': batch_id, 'enqueued': len(ids)}

    def evaluate(self, model_version, training_vehicle_ids, records):
        from .agents.evaluation import EvaluationAgent
        as_of = datetime.now(timezone.utc)
        result = EvaluationAgent().execute(records, model_version=model_version,
                   training_vehicle_ids=training_vehicle_ids, as_of=as_of).to_dict()
        inputs = canonical(dict(records=records, training_vehicle_ids=training_vehicle_ids))
        with self.db:
            cursor = self.db.execute('INSERT INTO evaluation_reports(created_at, model_version, inputs, outputs) VALUES (?, ?, ?, ?)',
                                   (as_of.isoformat(), model_version, inputs, canonical(result)))
        return {'report_id': cursor.lastrowid, 'as_of': as_of.isoformat(), 'result': result}
