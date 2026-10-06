"""Incremental asking-price memory and first-test research priorities.

An immutable compact projection learns each new archive event once. Neither
seller descriptions nor family asking prices establish repair scope or profit.
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from .archive import canonical
from .models import normalize
from .collection_geography import published_location

FIELDS = ('url','price_eur','price_kind','title','description','fuel','transmission',
          'generation','trim','version_text','year','mileage_km','condition','city',
          'province','seller_type','damage_severity','damage_indicators','active')
PAYMENT = re.compile(r'\b(?:anticipo|acconto|rata|rate mensili)\b|(?:€|eur)\s*/\s*mese', re.I)
DAMAGE = re.compile(r'\b(?:incidentat\w*|sinistrat\w*|danneggiat\w*|airbag.{0,15}(?:scoppi|esplos)|alluvionat\w*|incendiat\w*|uso ricambi|non marciante)\b',re.I)


def normalized(value):
    try:
        return normalize(value) if value else None
    except (ValueError, TypeError, AttributeError):
        return None


class PriceMemory:
    def __init__(self, db):
        self.db = db
        if db.dialect == 'sqlite':
            db.executescript('''CREATE TABLE IF NOT EXISTS price_observations (
                source TEXT NOT NULL,source_id TEXT NOT NULL,observed_at TEXT NOT NULL,
                active BOOLEAN NOT NULL,make TEXT,model TEXT,payload TEXT NOT NULL,
                PRIMARY KEY(source,source_id,observed_at));
                CREATE INDEX IF NOT EXISTS price_family ON price_observations(make,model,source,source_id,observed_at DESC);
                CREATE TABLE IF NOT EXISTS price_test_reports (
                run_id TEXT PRIMARY KEY,as_of TEXT NOT NULL,payload TEXT NOT NULL);''')

    def pending(self, as_of):
        return bool(self.db.execute('''SELECT 1 FROM listing_events e WHERE e.observed_at<=?
            AND NOT EXISTS (SELECT 1 FROM price_observations p WHERE p.source=e.source
              AND p.source_id=e.source_id AND p.observed_at=e.observed_at) LIMIT 1''',
            (as_of.isoformat(),)).fetchone())

    def sync(self, as_of, limit=100):
        rows = self.db.execute('''SELECT e.source,e.source_id,e.observed_at,e.url,e.active,e.payload
            FROM listing_events e WHERE e.observed_at<=? AND NOT EXISTS (
              SELECT 1 FROM price_observations p WHERE p.source=e.source AND p.source_id=e.source_id
              AND p.observed_at=e.observed_at) ORDER BY e.source,e.source_id,e.observed_at LIMIT ?''',
            (as_of.isoformat(),limit)).fetchall()
        compact = []
        for source, source_id, observed, url, active, encoded in rows:
            raw = self.db.json_decode(encoded)
            p = {key: raw.get(key) for key in FIELDS}
            p.update(source=source,source_id=source_id,observed_at=observed,url=url,active=active,
                     make=normalized(raw.get('make')),model=normalized(raw.get('model')))
            compact.append((source,source_id,observed,active,p['make'],p['model'],self.db.json_param(canonical(p))))
        if compact:
            with self.db:
                self.db.executemany('''INSERT INTO price_observations VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(source,source_id,observed_at) DO NOTHING''',compact)
        return len(rows)

    def current(self, as_of, *, make=None, model=None, source=None):
        cursor = ('','','')
        args = [(as_of-timedelta(days=30)).isoformat(),as_of.isoformat(),as_of.isoformat()]
        filters = ''
        for key,value in (('make',make),('model',model),('source',source)):
            if value is not None:
                filters += ' AND p.'+key+'=?'
                args.append(value)
        while True:
            rows = self.db.execute('''SELECT p.source,p.source_id,p.observed_at,p.payload
                FROM price_observations p WHERE p.active=? AND p.observed_at BETWEEN ? AND ?
                AND NOT EXISTS (SELECT 1 FROM price_observations n WHERE n.source=p.source
                    AND n.source_id=p.source_id AND n.observed_at>p.observed_at AND n.observed_at<=?)'''
                +filters+''' AND (p.source,p.source_id,p.observed_at)>(?,?,?)
                ORDER BY p.source,p.source_id,p.observed_at LIMIT 100''',
                (True,*args,*cursor)).fetchall()
            for s,i,t,p in rows:
                cursor = (s,i,t)
                yield self.db.json_decode(p)
            if len(rows)<100:
                break

    def first_test(self, run_id, as_of, limit=20):
        old = self.db.execute('SELECT payload FROM price_test_reports WHERE run_id=?',(run_id,)).fetchone()
        if old:
            return self.db.json_decode(old[0])
        if self.pending(as_of):
            return dict(status='waiting_for_price_memory',run_id=run_id)
        all_rows = list(self.current(as_of))
        groups = defaultdict(list)
        exclusions = defaultdict(int)
        eligible = []
        for p in all_rows:
            if not amount_usable(p) or not p.get('make') or not p.get('model'):
                exclusions['price_or_family_missing']+=1
                continue
            if risky(p):
                exclusions['damage_signal_or_unknown_damaged_scope']+=1
                continue
            if (type(p.get('year')) is not int or type(p.get('mileage_km')) is not int
                    or not normalized(p.get('fuel')) or not normalized(p.get('transmission'))):
                exclusions['year_mileage_fuel_or_gearbox_missing']+=1
                continue
            groups[(p['make'],p['model'],normalized(p.get('fuel')),normalized(p.get('transmission')))].append(p)
            eligible.append(p)
        bands = defaultdict(list)
        for p in eligible:
            if not 1000<=p['price_eur']<20000:
                exclusions['purchase_outside_test_range']+=1
                continue
            try:
                location=published_location(p.get('city'))
            except ValueError:
                exclusions['nearby_location_unresolved']+=1
                continue
            peers=[q for q in groups[(p['make'],p['model'],normalized(p.get('fuel')),normalized(p.get('transmission'))) ]
                   if (q['source'],q['source_id'])!=(p['source'],p['source_id'])
                   and q.get('seller_type')==p.get('seller_type')
                   and abs(q['year']-p['year'])<=1 and abs(q['mileage_km']-p['mileage_km'])<=20000]
            # Mixed sources/reposts could be the same vehicle. Collapse identical
            # seller asking specifications/amounts before counting analogies.
            unique={}
            for q in peers:
                identity=(q['year'],q['mileage_km'],q.get('version_text') or q.get('trim'),q['price_eur'],q.get('city'))
                unique.setdefault(identity,q)
            peers=list(unique.values())
            if len(peers)<8:
                exclusions['fewer_than_8_provisional_analogies']+=1
                continue
            prices=sorted(q['price_eur'] for q in peers)
            p25=prices[(len(prices)-1)//4]
            # Discount the lower quartile by 15% before even prioritizing checks.
            stressed=p25*85//100
            gap=stressed-p['price_eur']
            if gap<2000 or gap*100<p['price_eur']*25:
                exclusions['insufficient_gross_headroom']+=1
                continue
            band=min(3,p['price_eur']//5000)
            blockers=['total_purchase_price','identity_generation_engine_trim','damage_inspection',
                      'exact_variant_market_comparables','resale_value','all_operating_costs',
                      'supervisor_review','forecast_calibration']
            lead=dict(p,location=location,price_band=band,comparable_count=len(peers),
                published_median_eur=round(median(prices)),published_p25_eur=p25,
                stressed_asking_reference_eur=stressed,gross_headroom_before_all_costs_eur=gap,
                net_margin_eur=None,buy_recommendation=False,status='research_priority',
                blocking_reasons=blockers,
                sources=[dict(source=q['source'],source_id=q['source_id'],observed_at=q['observed_at'],
                              url=q['url'],price_eur=q['price_eur']) for q in peers],
                next_tasks=['confirm_source_price_availability_and_specifications','inspect_damage',
                            'identify_required_operations_and_oem_parts','search_verified_compatible_all_in_offers',
                            'obtain_external_bodyshop_and_labor_quotes','rerun_conservative_economics'])
            bands[band].append(lead)
        for band in bands:
            bands[band].sort(key=lambda p:(-p['gross_headroom_before_all_costs_eur']/p['price_eur'],
                                          -p['comparable_count'],p['source'],p['source_id']))
        selected=[]
        families=defaultdict(int)
        # Equal turns per available price band; never relax admission to fill it.
        while len(selected)<limit:
            added=False
            for band in range(4):
                while bands[band] and families[(bands[band][0]['make'],bands[band][0]['model'])]>=2:
                    bands[band].pop(0)
                if bands[band] and len(selected)<limit:
                    p=bands[band].pop(0)
                    families[(p['make'],p['model'])]+=1
                    selected.append(p); added=True
            if not added: break
        report=dict(run_id=run_id,as_of=as_of.isoformat(),status='completed_research_test',
                    active_recent_observations=len(all_rows),projected_events=self.db.execute(
                        'SELECT count(*) FROM price_observations WHERE observed_at<=?',(as_of.isoformat(),)).fetchone()[0],
                    exclusions=dict(exclusions),candidates=selected,approved_buys=0,
                    severity_policy='damaged_or_damage_signal_excluded_from_first_test',
                    basis='published_asking_amounts_not_resale_or_net_profit',
                    repair_search='awaiting_verified_identity_and_required_parts',
                    price_memory_incremental=True)
        with self.db:
            self.db.execute('INSERT INTO price_test_reports VALUES (?,?,?) ON CONFLICT(run_id) DO NOTHING',
                            (run_id,as_of.isoformat(),self.db.json_param(canonical(report))))
        return report


def amount_usable(p):
    amount=p.get('price_eur')
    return (type(amount) is int and amount>0 and p.get('price_kind') not in ('deposit','installment')
            and not (p.get('price_kind')!='total' and PAYMENT.search(str(p.get('title') or '')+' '+str(p.get('description') or ''))))


def risky(p):
    text=str(p.get('title') or '')+' '+str(p.get('description') or '')
    signals=[]
    for match in DAMAGE.finditer(text):
        prefix=text[max(0,match.start()-24):match.start()]
        if not re.search(r'\b(?:non|no|nessun[oa]?|senza|mai)\s+(?:è\s+|stata\s+|stato\s+)?$',prefix,re.I):
            signals.append(match.group())
    return (p.get('condition')=='damaged' or p.get('damage_severity')=='severe'
            or bool(p.get('damage_indicators')) or bool(signals))



class BackgroundPriceMemory:
    def __init__(self,path):
        self.path=path
        self.ready=False
        self.failures=0
        self.last_poll=None

    def step(self, run_id=None):
        import time
        if self.last_poll is not None and self.ready and time.monotonic()-self.last_poll<60:
            return None
        self.last_poll=time.monotonic()
        from .archive import Archive
        archive=None
        try:
            archive=Archive(self.path)
            memory=PriceMemory(archive.db)
            now=datetime.now(timezone.utc)
            count=memory.sync(now,limit=500)
            self.ready=not memory.pending(now)
            out=dict(price_memory='ready' if self.ready else 'building',projected_this_step=count)
            if self.ready and run_id:
                report=memory.first_test(run_id,now)
                if report.get('candidates'):
                    from .queue import Queue
                    from .agents.enrichment import listing_input
                    queue=Queue(self.path)
                    try:
                        batch='first-test-'+run_id
                        if not queue.db.execute('SELECT 1 FROM batches WHERE batch_id=?',(batch,)).fetchone():
                            records=[]
                            for p in report['candidates']:
                                original=archive.db.execute('SELECT url,payload FROM listing_events WHERE source=? AND source_id=? AND observed_at=?',
                                    (p['source'],p['source_id'],p['observed_at'])).fetchone()
                                records.append(dict(task='archive_enrichment',listing=listing_input(p['source'],p['source_id'],p['observed_at'],
                                    original[0],archive.db.json_decode(original[1]))))
                            queue.submit(batch,records)
                    finally:
                        queue.close()
                out.update(first_test=report['status'],run_id=run_id,candidates=len(report.get('candidates',[])),
                           approved_buys=report.get('approved_buys',0))
            self.failures=0
            return out if count or run_id else None
        except Exception as error:
            self.ready=False;self.failures+=1
            return dict(price_memory='retry_later',error_type=type(error).__name__,attempts=self.failures)
        finally:
            if archive: archive.close()
