"""Immutable marketplace catalogue, distinct from approved opportunities."""
import hashlib
import json
from dataclasses import fields
from datetime import datetime, timezone
from math import asin, cos, isfinite, radians, sin, sqrt
from urllib.parse import urlparse
from .database import Database, database_target
from .models import Listing, normalize


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def instant(value):
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError('Timestamp needs timezone')
    return timestamp.astimezone(timezone.utc)


def http_url(value):
    parsed = urlparse(value) if isinstance(value, str) else None
    if not parsed or parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('HTTP(S) URL required')
    return value


def coordinates(lat, lon):
    if (lat is None) != (lon is None):
        raise ValueError('Both coordinates required')
    if lat is not None:
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in (lat, lon)):
            raise ValueError('Coordinates must be finite numbers')
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError('Coordinates out of bounds')
    return lat, lon


def distance_km(a, b, x, y):
    delta = sin(radians(x-a)/2)**2 + cos(radians(a))*cos(radians(x))*sin(radians(y-b)/2)**2
    return 6371.0088 * 2 * asin(sqrt(min(1, max(0, delta))))


class Archive:
    def __init__(self, path=None):
        self.db = Database(path if path is not None else database_target())
        if self.db.dialect == 'sqlite':
            self.db.execute('PRAGMA busy_timeout=10000')
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS collection_pages (
                source TEXT NOT NULL, run_id TEXT NOT NULL, page_id TEXT NOT NULL,
                checksum TEXT NOT NULL, mode TEXT NOT NULL, scope TEXT NOT NULL,
                received_at TEXT NOT NULL, next_cursor TEXT, complete BOOLEAN NOT NULL,
                accepted INTEGER NOT NULL, rejected INTEGER NOT NULL, sequence INTEGER NOT NULL,
                PRIMARY KEY(source, run_id, page_id), UNIQUE(source, run_id, sequence));
            CREATE TABLE IF NOT EXISTS listing_events (
                source TEXT NOT NULL, source_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                url TEXT NOT NULL, active BOOLEAN NOT NULL, country TEXT NOT NULL,
                city TEXT, province TEXT, price_eur INTEGER, price_kind TEXT NOT NULL,
                latitude REAL, longitude REAL, payload TEXT NOT NULL,
                PRIMARY KEY(source, source_id, observed_at));
            CREATE TABLE IF NOT EXISTS collection_quarantine (
                source TEXT NOT NULL, run_id TEXT NOT NULL, page_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL, payload TEXT NOT NULL, issue TEXT NOT NULL,
                PRIMARY KEY(source, run_id, page_id, ordinal));
            CREATE INDEX IF NOT EXISTS listing_events_latest ON listing_events(source, source_id, observed_at DESC);
            CREATE INDEX IF NOT EXISTS listing_events_search ON listing_events(country, active, price_kind, price_eur);
            CREATE INDEX IF NOT EXISTS listing_events_location ON listing_events(city, province, latitude);
            """)
        elif not self.db.execute("SELECT to_regclass('deal_finder.listing_events')").fetchone()[0]:
            self.close()
            raise RuntimeError('National archive migration missing; run migrate')

    def close(self):
        self.db.close()

    def _event(self, source, record, as_of):
        if not isinstance(record, dict) or set(record) - {'source_id', 'observed_at', 'url', 'active', 'payload'}:
            raise ValueError('Invalid event envelope')
        source_id = record['source_id']
        if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 200:
            raise ValueError('source_id requires a string of at most 200 characters')
        observed = instant(record['observed_at'])
        if observed > as_of:
            raise ValueError('Event from the future')
        url = http_url(record['url'])
        active = record.get('active', True)
        if type(active) is not bool:
            raise ValueError('active must be boolean')
        payload = record['payload']
        if not isinstance(payload, dict):
            raise ValueError('Original payload must be an object')
        country = payload.get('country', 'IT')
        if not isinstance(country, str) or country.upper() != 'IT':
            raise ValueError('Only Italy is supported')
        price = payload.get('price_eur')
        if price is not None and (type(price) is not int or not 0 < price <= 10_000_000):
            raise ValueError('Price must be a positive integer in EUR')
        kind = payload.get('price_kind', 'unknown')
        if kind not in ('total', 'installment', 'deposit', 'unknown'):
            raise ValueError('Invalid price_kind')
        city = normalize(payload['city']) if payload.get('city') is not None else None
        province = normalize(payload['province']) if payload.get('province') is not None else None
        lat, lon = coordinates(payload.get('latitude'), payload.get('longitude'))
        images = payload.get('image_urls', [])
        if not isinstance(images, list) or len(images) > 1000:
            raise ValueError('At most 1000 image URLs allowed; original payload is retained in quarantine')
        for image in images:
            http_url(image)
        for key in ('title', 'description'):
            if not isinstance(payload.get(key, ''), str):
                raise ValueError(key + ' must be text')
        return (source, source_id.strip(), observed.isoformat(), url, active, 'IT', city, province,
                price, kind, lat, lon, self.db.json_param(canonical(payload)))

    def ingest(self, page, *, as_of=None):
        allowed = {'source', 'run_id', 'page_id', 'mode', 'scope', 'records', 'input_cursor', 'next_cursor', 'complete'}
        if not isinstance(page, dict) or set(page) - allowed:
            raise ValueError('Invalid collection page')
        source = normalize(page['source'])
        run_id, page_id = page['run_id'], page['page_id']
        for value in (source, run_id, page_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError('Identifiers require nonempty strings of at most 200 characters')
        mode, scope = page['mode'], page['scope']
        if mode not in ('initial', 'incremental') or not isinstance(scope, dict):
            raise ValueError('Collection requires initial/incremental mode and explicit scope')
        if scope.get('country') != 'IT':
            raise ValueError('Collection scope must explicitly specify country=IT')
        records = page['records']
        if not isinstance(records, list) or len(records) > 5000:
            raise ValueError('At most 5000 records per page')
        for key in ('input_cursor', 'next_cursor'):
            cursor = page.get(key)
            if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 4000):
                raise ValueError('Cursor must be a nonempty string or null')
        complete = page['complete']
        next_cursor = page.get('next_cursor')
        if type(complete) is not bool or complete != (next_cursor is None):
            raise ValueError('Completed page requires no cursor; unfinished page requires a cursor')
        encoded = canonical(page)
        if len(encoded.encode()) > 20_000_000:
            raise ValueError('Page exceeds 20 MB')
        checksum = hashlib.sha256(encoded.encode()).hexdigest()
        now = as_of or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError('as_of requires timezone')
        self.db.begin()
        try:
            self.db.batch_lock('collection:' + canonical([source, run_id]))
            old = self.db.execute('SELECT checksum FROM collection_pages WHERE source=? AND run_id=? AND page_id=?',
                                  (source, run_id, page_id)).fetchone()
            if old:
                if old[0] != checksum:
                    raise ValueError('Page ID already used with different content')
                self.db.commit()
                return dict(idempotent=True, **self.run_status(source, run_id))
            previous = self.db.execute('SELECT mode, scope, complete, next_cursor, sequence FROM collection_pages WHERE source=? AND run_id=? ORDER BY sequence DESC LIMIT 1',
                                       (source, run_id)).fetchone()
            sequence = 1
            if previous:
                if previous[0] != mode or self.db.json_decode(previous[1]) != scope or previous[2]:
                    raise ValueError('Run configuration changed or run already completed')
                if previous[3] != page.get('input_cursor'):
                    raise ValueError('Page does not continue the saved cursor')
                sequence = previous[4] + 1
            elif page.get('input_cursor') is not None:
                raise ValueError('First page requires a null input cursor')
            if not complete and next_cursor == page.get('input_cursor'):
                raise ValueError('Cursor did not advance')
            accepted, rejected = 0, 0
            for ordinal, record in enumerate(records):
                try:
                    with self.db.savepoint():
                        values = self._event(source, record, now)
                        self.db.execute('INSERT INTO listing_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source, source_id, observed_at) DO NOTHING', values)
                        original = self.db.execute('SELECT url, active, payload FROM listing_events WHERE source=? AND source_id=? AND observed_at=?', values[:3]).fetchone()
                        if original[0] != values[3] or bool(original[1]) != values[4] or self.db.json_decode(original[2]) != record['payload']:
                            raise ValueError('Conflicting immutable event')
                    accepted += 1
                except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
                    rejected += 1
                    self.db.execute('INSERT INTO collection_quarantine VALUES (?, ?, ?, ?, ?, ?)',
                                    (source, run_id, page_id, ordinal, self.db.json_param(canonical(record)), str(error)))
            self.db.execute('INSERT INTO collection_pages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                            (source, run_id, page_id, checksum, mode, self.db.json_param(canonical(scope)),
                             now.astimezone(timezone.utc).isoformat(), next_cursor, complete, accepted, rejected, sequence))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return dict(idempotent=False, **self.run_status(source, run_id))

    def run_status(self, source, run_id):
        rows = self.db.execute('SELECT mode, scope, next_cursor, complete, accepted, rejected FROM collection_pages WHERE source=? AND run_id=? ORDER BY sequence',
                               (normalize(source), run_id)).fetchall()
        if not rows:
            raise KeyError('Collection run not found')
        last = rows[-1]
        return dict(source=normalize(source), run_id=run_id, mode=last[0], scope=self.db.json_decode(last[1]),
                    next_cursor=last[2], complete=bool(last[3]), pages=len(rows),
                    accepted=sum(r[4] for r in rows), quarantined=sum(r[5] for r in rows),
                    market_coverage_verified=False)

    def history(self, source, source_id, *, offset=0, limit=100):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid pagination')
        rows = self.db.execute('SELECT observed_at, url, active, payload FROM listing_events WHERE source=? AND source_id=? ORDER BY observed_at DESC LIMIT ? OFFSET ?',
                               (normalize(source), source_id, limit, offset)).fetchall()
        return [dict(source=normalize(source), source_id=source_id, observed_at=r[0], url=r[1], active=bool(r[2]), payload=self.db.json_decode(r[3])) for r in rows]

    def normalized_envelope(self, source, source_id):
        rows = self.history(source, source_id, limit=1)
        if not rows:
            raise KeyError('Listing not found')
        event = rows[0]
        allowed = {f.name for f in fields(Listing)}
        data = {k: v for k, v in event['payload'].items() if k in allowed}
        data.update(source=event['source'], source_id=source_id, url=event['url'],
                    observed_at=event['observed_at'], active=event['active'],
                    price_kind=event['payload'].get('price_kind', 'unknown'))
        return {'listing': Listing.parse(data).to_dict()}

    def quality(self, source=None):
        """Coverage of the latest records, including uncertain prices and incomplete cars."""
        keys = ('title', 'description', 'make', 'model', 'version_text', 'generation',
                'trim', 'fuel', 'transmission', 'year', 'mileage_km', 'price_eur',
                'province', 'seller_type')
        def value(key):
            if self.db.dialect == 'postgres':
                return "(e.payload ->> '" + key + "')"
            return "json_extract(e.payload, '$." + key + "')"
        images = "COALESCE(jsonb_array_length(e.payload->'image_urls'), 0)" if self.db.dialect == 'postgres' else "COALESCE(json_array_length(e.payload, '$.image_urls'), 0)"
        aggregates = ['COUNT(*)'] + ["COALESCE(SUM(CASE WHEN COALESCE(CAST(" + value(k) + " AS TEXT), '') <> '' THEN 1 ELSE 0 END),0)" for k in keys]
        aggregates += [f'COALESCE(SUM(CASE WHEN {images}>0 THEN 1 ELSE 0 END),0)', f'COALESCE(SUM({images}),0)']
        params = (normalize(source),) if source else ()
        where = 'e.source=? AND ' if source else ''
        row = self.db.execute('SELECT ' + ','.join(aggregates) + ' FROM listing_events e WHERE ' + where + '''NOT EXISTS (
            SELECT 1 FROM listing_events n WHERE n.source=e.source AND n.source_id=e.source_id AND n.observed_at>e.observed_at)''', params).fetchone()
        return dict(listings=row[0], present=dict(zip(keys, row[1:1+len(keys)])),
                    missing={k: row[0]-row[i+1] for i,k in enumerate(keys)},
                    listings_with_photos=row[-2], photo_links=row[-1],
                    photo_storage='source_urls', forecasts_verified=False)

    def search(self, *, min_price=1000, max_price=50000, city=None, province=None,
               latitude=None, longitude=None, radius_km=None, offset=0, limit=100):
        if type(min_price) is not int or type(max_price) is not int or not 1000 <= min_price <= max_price <= 50000:
            raise ValueError('Price filters require 1000 <= min <= max <= 50000')
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid pagination')
        coordinates(latitude, longitude)
        if (radius_km is None) != (latitude is None):
            raise ValueError('Radius and coordinates must be provided together')
        if radius_km is not None and (isinstance(radius_km, bool) or not isinstance(radius_km, (int, float)) or not isfinite(radius_km) or not 0 < radius_km <= 1000):
            raise ValueError('Radius requires 0 < km <= 1000')
        predicates = ['e.active=?', "e.country='IT'", "e.price_kind='total'", 'e.price_eur BETWEEN ? AND ?']
        params = [True, min_price, max_price]
        for field, value in (('city', city), ('province', province)):
            if value is not None:
                predicates.append('e.' + field + '=?')
                params.append(normalize(value))
        if radius_km is not None:
            predicates.append('e.latitude BETWEEN ? AND ? AND e.longitude IS NOT NULL')
            params.extend([latitude-radius_km/110, latitude+radius_km/110])
        query = '''SELECT e.source, e.source_id, e.observed_at, e.url, e.active, e.payload,
                          e.latitude, e.longitude FROM listing_events e WHERE ''' + ' AND '.join(predicates) + '''
            AND NOT EXISTS (SELECT 1 FROM listing_events newer WHERE newer.source=e.source
                AND newer.source_id=e.source_id AND newer.observed_at>e.observed_at)
            ORDER BY e.observed_at DESC, e.source, e.source_id'''
        # Radius pagination is applied after exact great-circle distance filtering.
        if radius_km is None:
            query += ' LIMIT ? OFFSET ?'
            params.extend([limit, offset])
        items, matched = [], 0
        for r in self.db.execute(query, params):
            distance = distance_km(latitude, longitude, r[6], r[7]) if radius_km is not None else None
            if distance is not None and distance > radius_km:
                continue
            if radius_km is not None:
                matched += 1
                if matched <= offset:
                    continue
            items.append(dict(source=r[0], source_id=r[1], observed_at=r[2], url=r[3],
                              active=bool(r[4]), payload=self.db.json_decode(r[5]),
                              distance_km=round(distance, 3) if distance is not None else None))
            if len(items) >= limit:
                break
        return dict(kind='raw_catalogue', opportunities_verified=False, items=items, offset=offset)
