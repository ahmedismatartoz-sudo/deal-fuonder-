"""Documented TuttoTarghe adapter; explicit free plan required, no paid fallback."""
import json
import os
import re
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from .database import Database, database_target

PROVIDER = 'tuttotarghe-direct'
ENDPOINT = 'https://api.tuttotarghe.it/job/jobsync'
MAX_CALLS = 9  # Below advertised 10/day. Exclusive credential required.


def plate_number(value):
    if not isinstance(value, str):
        raise ValueError('Plate must be text')
    plate = value.upper().replace(' ', '').replace('-', '')
    if not re.fullmatch(r'[A-Z]{2}[0-9]{3}[A-Z]{2}', plate):
        raise ValueError('Only standard Italian car plates AA123AA are supported')
    return plate


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(token, plate):
    request = Request(ENDPOINT, data=json.dumps(dict(targhe=[plate], type=['details'], priority=False)).encode(),
                      headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json',
                               'Accept': 'application/json'}, method='POST')
    try:
        with build_opener(NoRedirect()).open(request, timeout=60) as response:
            content = response.read(1_000_001)
            if len(content) > 1_000_000:
                raise ValueError('Provider response exceeds size limit')
            return json.loads(content)
    except HTTPError as error:
        # Never expose token or echo untrusted upstream response bodies.
        raise RuntimeError('Plate provider HTTP '+str(error.code)) from None
    except (URLError, TimeoutError, json.JSONDecodeError):
        raise RuntimeError('Plate provider unavailable; no automatic retry') from None


def normalize_response(payload, plate, observed_at):
    if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
        raise ValueError('Unexpected provider response; raw response retained')
    rows = payload['results']
    matching = [row for row in rows if isinstance(row, dict) and row.get('targa') == plate]
    if len(matching) != 1:
        raise ValueError('Missing, duplicate or conflicting plate response')
    row = matching[0]
    if row.get('completed') is not True:
        raise ValueError('Lookup pending; no repeat submission')
    details = row.get('data', {}).get('details')
    if not isinstance(details, dict):
        raise ValueError('Vehicle details missing or schema unresolved')
    # Only explicitly named fields; missing schema dimensions stay unresolved.
    aliases = dict(make=('marca','make'), model=('modello','model'), trim=('allestimento','trim'),
                   engine_code=('codice_motore','engine_code'), fuel=('alimentazione','fuel'),
                   transmission=('cambio','transmission'), vin=('telaio','vin'),
                   generation=('generazione','generation'), year=('anno','year'))
    normalized, conflicts = {}, []
    for field, names in aliases.items():
        values = [details[k] for k in names if details.get(k) not in (None, '')]
        if values and any(str(x).strip().casefold() != str(values[0]).strip().casefold() for x in values):
            conflicts.append(field)
        else:
            normalized[field] = values[0] if values else None
    required = ('make','model','generation','trim','engine_code','transmission','year')
    missing = [key for key in required if normalized.get(key) is None]
    return dict(provider=PROVIDER, plate=plate, observed_at=observed_at,
                normalized=normalized, missing_fields=missing, conflicting_fields=conflicts,
                status='needs_review' if conflicts or missing else 'identified',
                identity_attestation=False, exact_part_fitment_confirmed=False,
                source_url='https://panel.tuttotarghe.it/public/docs')


class PlateLookup:
    def __init__(self, target=None, transport=fetch):
        self.db = Database(target if target is not None else database_target())
        self.transport = transport
        if self.db.dialect == 'sqlite':
            self.db.execute('PRAGMA busy_timeout=10000')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS plate_lookup_attempts (
                    id TEXT PRIMARY KEY, provider TEXT NOT NULL, plate TEXT NOT NULL, requested_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS plate_lookup_budget ON plate_lookup_attempts(provider,requested_at);
                CREATE TABLE IF NOT EXISTS plate_lookup_results (
                    attempt_id TEXT PRIMARY KEY REFERENCES plate_lookup_attempts(id),
                    provider TEXT NOT NULL, plate TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS plate_lookup_cache ON plate_lookup_results(provider,plate,received_at DESC);
            ''')

    def close(self):
        self.db.close()

    def lookup(self, value, *, as_of=None):
        plate = plate_number(value)
        now = as_of or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError('Timezone required')
        now = now.astimezone(timezone.utc)
        stamp = now.isoformat()
        token = os.getenv('DEAL_FINDER_TUTTOTARGHE_TOKEN', '').strip()
        # Explicit confirmation of free entitlement, exclusive credential and zero top-ups.
        free = os.getenv('DEAL_FINDER_PLATE_FREE_PLAN_CONFIRMED') == 'tuttotarghe-direct-10-per-day'
        with self.db:
            # Serialize globally across services. Reservation commits BEFORE network.
            if self.db.dialect == 'sqlite':
                self.db.begin()
            else:
                self.db.batch_lock('plate_lookup:'+PROVIDER)
            cached = self.db.execute('''SELECT payload FROM plate_lookup_results
                WHERE provider=? AND plate=? AND received_at>=? AND received_at<=?
                ORDER BY received_at DESC LIMIT 1''',
                (PROVIDER, plate, (now-timedelta(days=30)).isoformat(), stamp)).fetchone()
            if cached:
                data = self.db.json_decode(cached[0])
                return dict(data, cached=True)
            if not token or not free:
                return dict(status='configuration_required', provider=PROVIDER, cached=False,
                            reason='Exclusive free-plan API token and entitlement confirmation required',
                            paid_fallback=False)
            attempts = self.db.execute('''SELECT plate,requested_at FROM plate_lookup_attempts
                WHERE provider=? AND requested_at>=?''',
                (PROVIDER, (now-timedelta(hours=24)).isoformat())).fetchall()
            if len(attempts) >= MAX_CALLS:
                return dict(status='free_quota_exhausted', provider=PROVIDER, daily_cap=MAX_CALLS, paid_fallback=False)
            if any(x[0] == plate for x in attempts):
                return dict(status='recent_attempt_unresolved', provider=PROVIDER, paid_fallback=False)
            if attempts and now-datetime.fromisoformat(max(x[1] for x in attempts)) < timedelta(seconds=7):
                return dict(status='rate_limited', provider=PROVIDER, paid_fallback=False)
            attempt = uuid4().hex
            self.db.execute('INSERT INTO plate_lookup_attempts(id,provider,plate,requested_at) VALUES (?,?,?,?)',
                            (attempt,PROVIDER,plate,stamp))
        try:
            payload = self.transport(token, plate)
            try:
                result = normalize_response(payload, plate, stamp)
            except (ValueError, TypeError, AttributeError) as error:
                result = dict(status='needs_review', provider=PROVIDER, plate=plate, reason=str(error),
                              observed_at=stamp, identity_attestation=False, exact_part_fitment_confirmed=False)
            archived = dict(result, raw_response=payload, cached=False)
            with self.db:
                self.db.execute('''INSERT INTO plate_lookup_results(attempt_id,provider,plate,received_at,payload)
                    VALUES (?,?,?,?,?)''', (attempt,PROVIDER,plate,stamp,self.db.json_param(json.dumps(archived))))
            return archived
        except (RuntimeError, ValueError, TypeError) as error:
            return dict(status='provider_unavailable', provider=PROVIDER, reason=str(error),
                        reservation_retained=True, paid_fallback=False)
