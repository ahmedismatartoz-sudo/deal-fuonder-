"""Market-wide asking-price screening, separate from verified opportunities.

The immutable archive is the authority, including incomplete observations and
explicit removals. Scraped IDs never become verified vehicle identities.
"""
import hashlib
from dataclasses import fields
from datetime import datetime, timezone, timedelta
from .archive import Archive, canonical
from .models import Listing
from .pricing import estimate

VERSION = 'archive-asking-screen-v2'
COHORT = ('make', 'model', 'generation', 'trim', 'fuel', 'transmission', 'seller_type')


def asking_benchmark(target, pool, as_of):
    benchmark = estimate(target, pool, as_of=as_of)
    if benchmark['status'] != 'benchmark_available':
        benchmark = estimate(target, pool, as_of=as_of, scope='national')
    benchmark.update(evidence='unverified_source_asking_prices', source_scope=target.source)
    benchmark['warnings'].append('Seller claims and within-source duplicate vehicles require verification.')
    return benchmark


class Market:
    def __init__(self, path=None, *, db=None):
        self.archive = Archive(path) if db is None else None
        self.db = self.archive.db if db is None else db
        if db is not None:
            return  # Internal caller shares the already initialized database.
        if self.db.dialect == 'sqlite':
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS market_observations (
                source TEXT NOT NULL, source_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                signature TEXT NOT NULL, payload TEXT, quality_issue TEXT,
                make TEXT, model TEXT, generation TEXT, trim TEXT, fuel TEXT,
                transmission TEXT, seller_type TEXT, year INTEGER, mileage_km INTEGER,
                PRIMARY KEY(source, source_id, observed_at));
            CREATE INDEX IF NOT EXISTS market_cohort ON market_observations
                (source, make, model, generation, trim, fuel, transmission, seller_type, year, mileage_km);
            CREATE TABLE IF NOT EXISTS market_reviews (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL, source_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                version TEXT NOT NULL, as_of TEXT NOT NULL, signature TEXT NOT NULL, basis_signature TEXT NOT NULL,
                selected BOOLEAN NOT NULL, discount_fraction REAL, payload TEXT NOT NULL,
                UNIQUE(source, source_id, observed_at, version, as_of, basis_signature));
            CREATE INDEX IF NOT EXISTS market_reviews_latest ON market_reviews
                (source, source_id, version, as_of DESC);
            ''')
        elif not self.db.execute("SELECT to_regclass('deal_finder.market_observations')").fetchone()[0]:
            self.close()
            raise RuntimeError('Market screening migration missing; run migrate')

    def close(self):
        if self.archive is not None:
            self.archive.close()

    def sync(self, as_of):
        """Project every event before screening; page-sized atomic checkpoints."""
        projected = 0
        allowed = {f.name for f in fields(Listing)}
        while True:
            self.db.begin()
            try:
                self.db.batch_lock('market-projection')
                rows = self.db.execute('''SELECT e.source, e.source_id, e.observed_at,
                    e.url, e.active, e.payload FROM listing_events e
                    WHERE e.observed_at<=? AND NOT EXISTS (
                        SELECT 1 FROM market_observations m WHERE m.source=e.source
                        AND m.source_id=e.source_id AND m.observed_at=e.observed_at)
                    ORDER BY e.source, e.source_id, e.observed_at LIMIT 500''',
                    (as_of.isoformat(),)).fetchall()
                for source, source_id, stamp, url, active, original in rows:
                    original = self.db.json_decode(original)
                    data = {k: v for k, v in original.items() if k in allowed}
                    data.update(source=source, source_id=source_id, observed_at=stamp,
                                url=url, active=bool(active), vehicle_id=None,
                                price_kind=original.get('price_kind', 'unknown'))
                    payload, issue = None, None
                    specs = [None] * 9
                    try:
                        listing = Listing.parse(data)
                        payload = listing.to_dict()
                        specs = [getattr(listing, k) for k in (*COHORT, 'year', 'mileage_km')]
                    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
                        issue = str(error)
                    # Refresh timestamps alone do not enqueue the same analysis.
                    stable = {k: v for k, v in data.items() if k != 'observed_at'}
                    signature = hashlib.sha256(canonical(stable).encode()).hexdigest()
                    self.db.execute('INSERT INTO market_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (source, source_id, stamp, signature,
                         None if payload is None else self.db.json_param(canonical(payload)), issue, *specs))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            projected += len(rows)
            if len(rows) < 500:
                return projected

    def _latest(self, alias='m'):
        # Query the raw authority, not just usable projections. A seller's new
        # incomplete specification or removal must supersede its old good data.
        return f'''NOT EXISTS (SELECT 1 FROM listing_events n
            WHERE n.source={alias}.source AND n.source_id={alias}.source_id
            AND n.observed_at>{alias}.observed_at AND n.observed_at<=?)'''

    def comparables(self, target, as_of):
        predicates = ' AND '.join(f'm.{field}=?' for field in COHORT)
        rows = self.db.execute('''SELECT m.payload FROM market_observations m
            JOIN listing_events e ON e.source=m.source AND e.source_id=m.source_id
            AND e.observed_at=m.observed_at WHERE m.source=? AND ''' + predicates + '''
            AND m.year BETWEEN ? AND ? AND m.mileage_km BETWEEN ? AND ?
            AND m.quality_issue IS NULL AND e.active=? AND e.price_kind='total'
            AND m.observed_at BETWEEN ? AND ? AND ''' + self._latest(),
            (target.source, *(getattr(target, k) for k in COHORT), target.year-1, target.year+1,
             max(0, target.mileage_km-20000), target.mileage_km+20000, True,
             (as_of-timedelta(days=30)).isoformat(), as_of.isoformat(), as_of.isoformat()))
        return [Listing.parse(self.db.json_decode(r[0])) for r in rows]

    def screen(self, target, as_of):
        pool = self.comparables(target, as_of)
        basis_signature = hashlib.sha256(canonical(sorted(
            [item.to_dict() for item in pool], key=lambda v: (v['source'], v['source_id']))).encode()).hexdigest()
        benchmark = asking_benchmark(target, pool, as_of)
        # One source at a time avoids counting the same car across different
        # marketplaces when vehicle identities have not been independently verified.
        selected, discount = False, None
        reason = 'insufficient_comparables'
        if not target.active or target.price_kind != 'total' or not 1000 <= target.price_eur <= 50000:
            reason = 'outside_purchase_scope'
        elif target.condition == 'unknown':
            reason = 'condition_unresolved'
        elif benchmark['status'] == 'benchmark_available':
            lower = benchmark['observed_range_eur']['p25']
            discount = round((lower-target.price_eur)/lower, 6)
            selected = (lower-target.price_eur)*10 >= lower
            reason = 'below_asking_market' if selected else 'discount_too_small'
        return dict(listing=target.to_dict(), selected=selected, reason=reason,
                    route='verification' if selected else 'enrichment' if reason in ('insufficient_comparables', 'condition_unresolved') else 'screened_out',
                    basis_signature=basis_signature,
                    discount_fraction=discount, benchmark=benchmark,
                    status='candidate_for_verification' if selected else 'screened',
                    recommended_resale_price_eur=None, profit_forecast_eur=None,
                    sale_days_forecast=None, supervisor_approved=False,
                    repairs_status='documented_inspection_required',
                    forecast_enabled=False, publishable=False)

    @staticmethod
    def batch_id(review):
        key = [review['listing']['source'], review['listing']['source_id'],
               review['listing']['observed_at'], review['basis_signature'], VERSION]
        return 'market-' + hashlib.sha256(canonical(key).encode()).hexdigest()

    def scan(self, *, mode='incremental', as_of=None, queue=None):
        """Initial/full scans all current targets; incremental scans new/changed.

        All observations are projected first, so even the first target sees the
        entire base. There is no target limit of 100 or truncated comparable set.
        """
        if mode not in ('initial', 'incremental', 'full'):
            raise ValueError('Invalid market scan mode')
        now = as_of or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError('Scan requires timezone')
        now = now.astimezone(timezone.utc)
        projected = self.sync(now)
        scanned = selected = unchanged = unusable = queued = 0
        cursor = ('', '')
        while True:
            rows = self.db.execute('''SELECT m.source, m.source_id, m.observed_at,
                m.signature, m.payload, m.quality_issue FROM market_observations m
                WHERE (m.source>? OR (m.source=? AND m.source_id>?))
                AND m.observed_at<=? AND ''' + self._latest() + '''
                ORDER BY m.source, m.source_id LIMIT 500''',
                (cursor[0], cursor[0], cursor[1], now.isoformat(), now.isoformat())).fetchall()
            for source, source_id, stamp, signature, payload, issue in rows:
                cursor = source, source_id
                if issue:
                    unusable += 1
                    continue
                target = Listing.parse(self.db.json_decode(payload))
                if not target.active or (now-datetime.fromisoformat(stamp)).total_seconds() > 30*86400:
                    continue
                old = self.db.execute('''SELECT signature, basis_signature FROM market_reviews
                    WHERE source=? AND source_id=? AND version=? AND as_of<=?
                    ORDER BY as_of DESC, id DESC LIMIT 1''', (source, source_id, VERSION, now.isoformat())).fetchone()
                review = self.screen(target, now)
                if (mode == 'incremental' and old and old[0] == signature
                        and old[1] == review['basis_signature']):
                    unchanged += 1
                    continue
                with self.db:
                    self.db.execute('''INSERT INTO market_reviews
                        (source, source_id, observed_at, version, as_of, signature, basis_signature, selected, discount_fraction, payload)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source, source_id, observed_at, version, as_of, basis_signature) DO NOTHING''',
                        (source, source_id, stamp, VERSION, now.isoformat(), signature, review['basis_signature'],
                         review['selected'], review['discount_fraction'], self.db.json_param(canonical(review))))
                scanned += 1
                selected += int(review['selected'])
            if len(rows) < 500:
                break
        # Reconcile after the durable report commit. A crash between the report
        # and queue submission is recovered on the next cycle; deterministic batch
        # IDs make retries harmless. The queue cannot acquire scraped attestations.
        if queue is not None:
            for review in self.pending_candidates(now):
                listing = dict(review['listing'])
                original = self.db.execute('''SELECT payload FROM listing_events
                    WHERE source=? AND source_id=? AND observed_at=?''',
                    (listing['source'], listing['source_id'], listing['observed_at'])).fetchone()
                vehicle_id = self.db.json_decode(original[0]).get('vehicle_id')
                # Keep a source-claimed identity on the target so an operator can
                # attest it later. It is never used to deduplicate the price base.
                if isinstance(vehicle_id, str) and vehicle_id.strip():
                    listing['vehicle_id'] = vehicle_id.strip()
                receipt = queue.submit(self.batch_id(review), [{'listing': listing}])
                queued += int(not receipt['idempotent'])
        return dict(mode=mode, as_of=now.isoformat(), projected=projected, scanned=scanned,
                    selected=selected, unchanged=unchanged, unusable=unusable,
                    queued=queued, forecast_enabled=False, publication_enabled=False)

    def pending_candidates(self, as_of):
        if self.db.execute('''SELECT 1 FROM listing_events e WHERE e.observed_at<=?
            AND NOT EXISTS (SELECT 1 FROM market_observations m WHERE m.source=e.source
                AND m.source_id=e.source_id AND m.observed_at=e.observed_at) LIMIT 1''',
                (as_of.isoformat(),)).fetchone():
            return
        rows = self.db.execute('''SELECT r.payload FROM market_reviews r
            JOIN market_observations m ON m.source=r.source AND m.source_id=r.source_id
            JOIN listing_events e ON e.source=m.source AND e.source_id=m.source_id
                AND e.observed_at=m.observed_at
            WHERE r.version=? AND r.as_of<=? AND r.selected=? AND r.signature=m.signature
                AND e.active=? AND m.observed_at BETWEEN ? AND ? AND ''' + self._latest() + '''
                AND NOT EXISTS (SELECT 1 FROM market_reviews newer
                    WHERE newer.source=r.source AND newer.source_id=r.source_id
                    AND newer.version=r.version AND (newer.as_of>r.as_of OR
                        (newer.as_of=r.as_of AND newer.id>r.id)) AND newer.as_of<=?)
            ORDER BY r.discount_fraction DESC, r.source, r.source_id''',
            (VERSION, as_of.isoformat(), True, True, (as_of-timedelta(days=30)).isoformat(),
             as_of.isoformat(), as_of.isoformat(), as_of.isoformat()))
        for row in rows:
            review = self.db.json_decode(row[0])
            current = self.screen(Listing.parse(review['listing']), as_of)
            # Until the scanner catches up, a changed/removal event invalidates
            # the old benchmark instead of leaving a stale candidate visible.
            if current['basis_signature'] == review['basis_signature'] and current['selected']:
                yield review

    def candidates(self, *, offset=0, limit=100, city=None, province=None, as_of=None):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid pagination')
        from .models import normalize
        city = normalize(city) if city else None
        province = normalize(province) if province else None
        result = []
        count = 0
        for review in self.pending_candidates(as_of or datetime.now(timezone.utc)):
            listing = review['listing']
            if city and listing.get('city') != city or province and listing['province'] != province:
                continue
            if count >= offset and len(result) < limit:
                result.append(dict(review, batch_id=self.batch_id(review)))
            count += 1
        return dict(items=result, count=count, offset=offset, opportunities_verified=False)

    def status(self):
        unprojected = self.db.execute('''SELECT COUNT(*) FROM listing_events e WHERE NOT EXISTS
            (SELECT 1 FROM market_observations m WHERE m.source=e.source AND m.source_id=e.source_id
            AND m.observed_at=e.observed_at)''').fetchone()[0]
        issues = dict(self.db.execute('SELECT quality_issue, COUNT(*) FROM market_observations WHERE quality_issue IS NOT NULL GROUP BY quality_issue'))
        return dict(unprojected_events=unprojected, normalization_issues=issues,
                    reviews=self.db.execute('SELECT COUNT(*) FROM market_reviews').fetchone()[0],
                    version=VERSION, forecast_enabled=False)

    def queue_context(self, target, as_of):
        """Only source records matching the immutable archive use this lane."""
        current = self.db.execute('''SELECT observed_at FROM listing_events
            WHERE source=? AND source_id=? AND observed_at<=? ORDER BY observed_at DESC LIMIT 1''',
            (*target.identity, as_of.isoformat())).fetchone()
        if current is None:
            return None
        if self.db.execute('''SELECT 1 FROM listing_events e WHERE e.observed_at<=?
            AND NOT EXISTS (SELECT 1 FROM market_observations m WHERE m.source=e.source
                AND m.source_id=e.source_id AND m.observed_at=e.observed_at) LIMIT 1''',
                (as_of.isoformat(),)).fetchone():
            raise ValueError('Archive projection incomplete; run market-scan before analysis')
        if current[0] != target.observed_at:
            raise ValueError('Target snapshot superseded in the source archive')
        row = self.db.execute('''SELECT payload, quality_issue FROM market_observations
            WHERE source=? AND source_id=? AND observed_at=?''', (*target.identity, target.observed_at)).fetchone()
        if row is None or row[1]:
            raise ValueError('Archive projection missing or unusable; run market-scan first')
        normalized = dict(target.to_dict(), vehicle_id=None)
        if normalized != self.db.json_decode(row[0]):
            raise ValueError('Analysis listing conflicts with its immutable source observation')
        return self.comparables(target, as_of)
