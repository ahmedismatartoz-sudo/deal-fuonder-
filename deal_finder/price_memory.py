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

SCREENING_VERSION = 'variant-and-damage-screening-v2'

FIELDS = ('url','price_eur','price_kind','title','description','fuel','transmission',
          'generation','trim','version_text','year','mileage_km','condition','city',
          'province','seller_type','damage_severity','damage_indicators','active',
          'power_hp','displacement_cc')
PAYMENT = re.compile(r'\b(?:anticipo|acconto|rata|rate mensili)\b|(?:€|eur)\s*/\s*mese', re.I)
DAMAGE = re.compile(r'\b(?:incidentat\w*|sinistrat\w*|danneggiat\w*|grandin\w*|airbag.{0,15}(?:scoppi|esplos)|alluvionat\w*|incendiat\w*|uso ricambi|non marciante|motore\s+(?:da\s+(?:cambiare|sostituire|rifare)|rotto|fuso)|(?:problemi|guasto|guasti)\s+(?:al\s+)?(?:motore|cambio)|carrozzeria\s+scolorita|crepa\s+sul\s+parafango)\b',re.I)


def postgres_projection_sql():
    # Choose missing identities using the covering primary-key indexes before
    # touching any original JSON. jsonb_to_record extracts only named fields;
    # jsonb_each also materializes the bulky original/image values we discard.
    columns = ','.join(field+' jsonb' for field in (*FIELDS,'make','model'))
    def key(field):
        return "CASE WHEN jsonb_typeof(e.payload->'"+field+"')='string' THEN nullif(lower(regexp_replace(btrim(e.payload->>'"+field+"'), '\\s+', ' ', 'g')),'') END"
    make, model = key('make'), key('model')
    return f"""WITH projection_lock AS MATERIALIZED (
        SELECT pg_try_advisory_xact_lock(1649763002) AS acquired),
        missing_keys AS MATERIALIZED (
        SELECT e.source,e.source_id,e.observed_at FROM listing_events e
        WHERE (SELECT acquired FROM projection_lock) AND e.observed_at<=?
        AND (e.source,e.source_id,e.observed_at)>(?,?,?)
        AND NOT EXISTS (SELECT 1 FROM price_observations p WHERE p.source=e.source
            AND p.source_id=e.source_id AND p.observed_at=e.observed_at)
        ORDER BY e.source,e.source_id,e.observed_at LIMIT ?),
        thin AS MATERIALIZED (
        SELECT e.source,e.source_id,e.observed_at,e.url,e.active,to_jsonb(f) AS payload
        FROM missing_keys k JOIN listing_events e USING(source,source_id,observed_at)
        CROSS JOIN LATERAL jsonb_to_record(e.payload) AS f({columns}))
        , inserted AS (
        INSERT INTO price_observations(source,source_id,observed_at,active,make,model,payload)
        SELECT e.source,e.source_id,e.observed_at,e.active,{make},{model},
            e.payload || jsonb_build_object('url',e.url,'active',e.active,'make',{make},'model',{model})
        FROM thin e ON CONFLICT(source,source_id,observed_at) DO NOTHING RETURNING 1)
        SELECT (SELECT count(*) FROM inserted),k.source,k.source_id,k.observed_at
        FROM (SELECT 1) seed LEFT JOIN LATERAL (
            SELECT source,source_id,observed_at FROM missing_keys
            ORDER BY source DESC,source_id DESC,observed_at DESC LIMIT 1) k ON true"""


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

    def sync(self, as_of, limit=100, *, after=None):
        after=after or ('','','0001-01-01T00:00:00+00:00')
        if self.db.dialect == 'postgres':
            result=self.db.execute(postgres_projection_sql(),(as_of.isoformat(),*after,limit)).fetchone()
            self.next_cursor=tuple(result[1:]) if result[1] is not None else None
            return result[0]
        rows = self.db.execute('''SELECT e.source,e.source_id,e.observed_at,e.url,e.active,e.payload
            FROM listing_events e WHERE e.observed_at<=? AND (e.source,e.source_id,e.observed_at)>(?,?,?) AND NOT EXISTS (
              SELECT 1 FROM price_observations p WHERE p.source=e.source AND p.source_id=e.source_id
              AND p.observed_at=e.observed_at) ORDER BY e.source,e.source_id,e.observed_at LIMIT ?''',
            (as_of.isoformat(),*after,limit)).fetchall()
        self.next_cursor=tuple(rows[-1][:3]) if rows else None
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
        cursor = ('','','0001-01-01T00:00:00+00:00')
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
                value=self.db.json_decode(p)
                value.update(source=s,source_id=i,observed_at=t)
                yield value
            if len(rows)<100:
                break

    def first_test(self, run_id, as_of, limit=20):
        old = self.db.execute('SELECT payload FROM price_test_reports WHERE run_id=?',(run_id,)).fetchone()
        if old:
            cached = self.db.json_decode(old[0])
            if cached.get('screening_version') == SCREENING_VERSION:
                return cached
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
                   and same_variant(p,q)
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
        report=dict(run_id=run_id,as_of=as_of.isoformat(),screening_version=SCREENING_VERSION,status='completed_research_test',
                    active_recent_observations=len(all_rows),projected_events=self.db.execute(
                        'SELECT count(*) FROM price_observations WHERE observed_at<=?',(as_of.isoformat(),)).fetchone()[0],
                    exclusions=dict(exclusions),candidates=selected,approved_buys=0,
                    severity_policy='damaged_or_damage_signal_excluded_from_first_test',
                    basis='published_asking_amounts_not_resale_or_net_profit',
                    repair_search='awaiting_verified_identity_and_required_parts',
                    price_memory_incremental=True)
        if self.pending(as_of):
            return dict(status='waiting_for_price_memory',run_id=run_id)
        with self.db:
            self.db.execute('INSERT INTO price_test_reports VALUES (?,?,?) ON CONFLICT(run_id) DO UPDATE SET as_of=excluded.as_of,payload=excluded.payload',
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


def same_variant(p, q):
    """Strict research analogies; missing identity never proves equivalence.

    Equal published version text is provisional, not an identity attestation.
    Prefer losing an analogy to mixing GR/ST/4x4 or different engines.
    """
    for key in ('generation', 'trim', 'power_hp', 'displacement_cc'):
        a, b = p.get(key), q.get(key)
        if a is not None and b is not None and normalized(str(a)) != normalized(str(b)):
            return False
    a, b = normalized(p.get('version_text')), normalized(q.get('version_text'))
    if a or b:
        return bool(a and b and a == b)
    return all(normalized(p.get(key)) and normalized(p.get(key)) == normalized(q.get(key))
               for key in ('generation', 'trim'))



class BackgroundPriceMemory:
    def __init__(self,path):
        self.path=path
        self.ready=False
        self.failures=0
        self.last_poll=None
        self.batch_size=25
        self.retry_at=None
        self.sync_cursor=None

    def step(self, run_id=None):
        import time
        started=time.monotonic()
        if self.retry_at is not None and started<self.retry_at:
            return None
        if self.last_poll is not None and self.ready and time.monotonic()-self.last_poll<60:
            return None
        self.last_poll=time.monotonic()
        from .archive import Archive
        archive=None
        stage='open'
        try:
            archive=Archive(self.path)
            memory=PriceMemory(archive.db)
            now=datetime.now(timezone.utc)
            stage='project_new_observations'
            projection_started=time.monotonic()
            count=memory.sync(now,limit=self.batch_size,after=self.sync_cursor)
            self.sync_cursor=memory.next_cursor
            projection_seconds=time.monotonic()-projection_started
            stage='check_completion'
            # A full batch cannot prove completion. Only a short/empty batch
            # needs the global missing-event check; this also catches a late
            # arrival behind the cursor before any comparison is allowed.
            self.ready=count<self.batch_size and not memory.pending(now)
            out=dict(price_memory='ready' if self.ready else 'building',projected_this_step=count)
            if self.ready and run_id:
                stage='compare_archive'
                report=memory.first_test(run_id,now)
                if report['status']=='waiting_for_price_memory':
                    self.ready=False
                    return dict(price_memory='building',projected_this_step=count)
                stage='enqueue_verifications'
                if report.get('candidates'):
                    from .queue import Queue
                    from .agents.enrichment import listing_input
                    queue=Queue(self.path)
                    try:
                        batch='first-test-'+run_id+'-'+SCREENING_VERSION
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
            self.retry_at=None
            # A successful slow query is still too much work for this database.
            # Stay comfortably below the statement limit instead of doubling
            # every success back to the same batch that just timed out.
            if projection_seconds>5:
                self.batch_size=max(1,self.batch_size//2)
            elif projection_seconds<2:
                self.batch_size=min(100,self.batch_size*2)
            out['projection_seconds']=round(projection_seconds,3)
            out['next_projection_batch']=self.batch_size
            return out if count or run_id else None
        except Exception as error:
            self.ready=False;self.failures+=1
            if stage=='project_new_observations':
                self.batch_size=max(1,self.batch_size//2)
            delay=min(60,2**min(self.failures,6))
            self.retry_at=time.monotonic()+delay
            out=dict(price_memory='retry_later',stage=stage,error_type=type(error).__name__,
                     attempts=self.failures,next_projection_batch=self.batch_size,retry_after_seconds=delay)
            sqlstate=getattr(error,'sqlstate',None)
            if sqlstate: out['db_sqlstate']=sqlstate
            db_error_type=getattr(error,'db_error_type',None)
            if db_error_type: out['db_error_type']=db_error_type
            return out
        finally:
            if archive: archive.close()
