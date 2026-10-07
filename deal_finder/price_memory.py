"""Incremental asking-price memory and first-test research priorities.

An immutable compact projection learns each new archive event once. Neither
seller descriptions nor family asking prices establish repair scope or profit.
"""
import re
import os
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from .archive import canonical
from .models import normalize
from .collection_geography import published_location

SCREENING_VERSION = 'broad-discovery-streaming-v14'


def autonomous_enabled():
    return os.getenv('DEAL_FINDER_AUTONOMOUS_SCREENING_ENABLED') == '1'


def priority_batch_prefix(*, profile=None, state=None):
    if state is None:
        from .agent_runtime import connections
        state = 'identity-ready' if connections()['photo_web_provider_configured'] else 'identity-blocked'
    return 'archive-priority-'+SCREENING_VERSION+'-'+research_policy(profile)['profile']+'-'+state+'-'


def priority_observation_batch(candidate, **options):
    key = canonical([candidate[k] for k in ('source','source_id','observed_at')])
    return priority_batch_prefix(**options)+hashlib.sha256(key.encode()).hexdigest()[:24]


def research_policy(profile=None):
    profile = profile or os.getenv('DEAL_FINDER_FIRST_TEST_PROFILE', 'strict')
    if profile=='discovery':
        return dict(profile=profile,minimum_comparables=2,year_tolerance=3,
                    mileage_tolerance_km=60000,minimum_headroom_eur=2000,
                    asking_discount_policy='purchase_price_tiers_2000_3000_4000_5000',
                    minimum_discount_percent=0,asking_stress_percent=0,
                    final_conservative_filter_required=True)
    if profile in ('exploratory','opportunities'):
        return dict(profile=profile, minimum_comparables=3, year_tolerance=2,
                    mileage_tolerance_km=40000, minimum_headroom_eur=500,
                    minimum_discount_percent=10, asking_stress_percent=0)
    if profile != 'strict':
        raise ValueError('Unknown research screening profile')
    return dict(profile=profile, minimum_comparables=8, year_tolerance=1,
                mileage_tolerance_km=20000, minimum_headroom_eur=2000,
                minimum_discount_percent=25, asking_stress_percent=15)


def priority_batch_id(run_id):
    """A configured provider gets one fresh attempt after blocked enrichment.

    Do not reuse jobs completed without a key, or retry paid research on every
    poll/restart. Readiness is a boolean, never a credential in the batch ID.
    """
    from .agent_runtime import connections
    state = 'identity-ready' if connections()['photo_web_provider_configured'] else 'identity-blocked'
    return 'first-test-'+run_id+'-'+SCREENING_VERSION+'-'+research_policy()['profile']+'-'+state

FIELDS = ('url','price_eur','price_kind','title','description','fuel','transmission',
          'generation','trim','version_text','year','mileage_km','condition','city',
          'province','seller_type','damage_severity','damage_indicators','active',
          'power_hp','power_kw','displacement_cc','engine_code','engine_name','drivetrain','body_type','damage_source_claims','collection_price_screen','detail_fetched')
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
        from .identity_source_cache import IdentitySourceCache
        if IdentitySourceCache(self.db).pending(as_of):return True
        return bool(self.db.execute('''SELECT 1 FROM listing_events e WHERE e.observed_at<=?
            AND NOT EXISTS (SELECT 1 FROM price_observations p WHERE p.source=e.source
              AND p.source_id=e.source_id AND p.observed_at=e.observed_at) LIMIT 1''',
            (as_of.isoformat(),)).fetchone())

    def sync(self, as_of, limit=100, *, after=None, recover_identity=True):
        after=after or ('','','0001-01-01T00:00:00+00:00')
        if self.db.dialect == 'postgres':
            result=self.db.execute(postgres_projection_sql(),(as_of.isoformat(),*after,limit)).fetchone()
            self.next_cursor=tuple(result[1:]) if result[1] is not None else None
            from .identity_source_cache import IdentitySourceCache
            self.cached_source_count=IdentitySourceCache(self.db).sync(as_of,limit) if recover_identity else 0
            return result[0] or self.cached_source_count
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
        from .identity_source_cache import IdentitySourceCache
        self.cached_source_count=IdentitySourceCache(self.db).sync(as_of,limit) if recover_identity else 0
        return len(rows) or self.cached_source_count

    def current(self, as_of, *, make=None, model=None, source=None, recover_identity=True):
        cursor = ('','','0001-01-01T00:00:00+00:00')
        args = [(as_of-timedelta(days=30)).isoformat(),as_of.isoformat(),as_of.isoformat()]
        filters = ''
        for key,value in (('make',make),('model',model),('source',source)):
            if value is not None:
                filters += ' AND p.'+key+'=?'
                args.append(value)
        while True:
            page_sql = ('''SELECT p.source,p.source_id,p.observed_at,p.payload
                FROM price_observations p WHERE p.active=? AND p.observed_at BETWEEN ? AND ?
                AND NOT EXISTS (SELECT 1 FROM price_observations n WHERE n.source=p.source
                    AND n.source_id=p.source_id AND n.observed_at>p.observed_at AND n.observed_at<=?)'''
                +filters+''' AND (p.source,p.source_id,p.observed_at)>(?,?,?)
                ORDER BY p.source,p.source_id,p.observed_at LIMIT 100''')
            if not recover_identity:
                rows=self.db.execute(page_sql.replace("LIMIT 100", "LIMIT 1000"),(True,*args,*cursor)).fetchall()
                for source_name,identifier,observed,payload in rows:
                    cursor=(source_name,identifier,observed)
                    value=self.db.json_decode(payload)
                    value.update(source=source_name,source_id=identifier,observed_at=observed)
                    from .vehicle_identity import enrich
                    yield enrich(value,source_url=value.get("url"))
                if len(rows)<1000:break
                continue
            from .identity_source_cache import IdentitySourceCache,VERSION as source_version
            IdentitySourceCache(self.db)
            materialized='MATERIALIZED ' if self.db.dialect=='postgres' else ''
            sql='WITH source_page AS '+materialized+'('+page_sql+""" )
                SELECT p.source,p.source_id,p.observed_at,p.payload,c.seller_type,c.source_fields
                FROM source_page p LEFT JOIN identity_source_cache c ON c.version=?
                    AND c.source=p.source AND c.source_id=p.source_id AND c.observed_at=p.observed_at
                ORDER BY p.source,p.source_id,p.observed_at"""
            rows=self.db.execute(sql,(True,*args,*cursor,source_version)).fetchall()
            for s,i,t,p,*facts in rows:
                cursor = (s,i,t)
                value=self.db.json_decode(p)
                value.update(source=s,source_id=i,observed_at=t)
                # A missing cache means recovery is pending, not source absence.
                # Never use an incomplete legacy projection as a comparison.
                if s == 'autoscout24':
                    if not facts or facts[1] is None:
                        continue
                    if facts:
                        seller={'PrivateSeller':'private','Private':'private','Dealer':'dealer'}.get(facts[0])
                        if seller: value['seller_type']=seller
                        value['identity_source_fields']=self.db.json_decode(facts[1]) if facts[1] else {}
                from .vehicle_identity import enrich
                value=enrich(value,source_url=value.get('url'))
                value.pop('identity_source_fields',None)
                yield value
            if len(rows)<100:
                break

    def automatic_run_id(self, base, as_of):
        revision = self.db.execute('SELECT count(*),max(observed_at) FROM price_observations WHERE observed_at<=?',
                                   (as_of.isoformat(),)).fetchone()
        digest = hashlib.sha256(str(tuple(revision)).encode()).hexdigest()[:16]
        return base+'-auto-'+as_of.date().isoformat()+'-'+digest

    def first_test(self, run_id, as_of, limit=20, *, profile=None, autonomous=False):
        policy = research_policy(profile)
        storage_run_id = run_id+'-'+SCREENING_VERSION+'-'+policy['profile']
        old = self.db.execute('SELECT payload FROM price_test_reports WHERE run_id=?',(storage_run_id,)).fetchone()
        if old:
            cached = self.db.json_decode(old[0])
            if cached.get('screening_version') == SCREENING_VERSION:
                return cached
        if policy['profile']!='discovery' and self.pending(as_of):
            return dict(status='waiting_for_price_memory',run_id=run_id)
        all_rows = list(self.current(as_of,recover_identity=policy['profile']!='discovery'))
        if policy['profile']=='discovery':
            from .opportunity_discovery import build_report
            discovery_limit=max(1,min(500,int(os.getenv('DEAL_FINDER_DISCOVERY_LIMIT',str(limit)))))
            report=build_report(all_rows,run_id,as_of,SCREENING_VERSION,policy,discovery_limit,autonomous)
            report['identity_recovery_required_for_final_filter']=True
            report['archive_projection_basis']='available_compact_observations'
            with self.db:
                self.db.execute('INSERT INTO price_test_reports VALUES (?,?,?) ON CONFLICT(run_id) DO NOTHING',
                    (storage_run_id,as_of.isoformat(),self.db.json_param(canonical(report))))
            return report
        groups = defaultdict(list)
        exclusions = defaultdict(int)
        eligible = []
        learning=[]
        from .damage_screening import classify,feasibility,MIX,quotas
        opportunity_profile=policy['profile']=='opportunities'
        damage_counts=defaultdict(int)
        for p in all_rows:
            p['damage_screening']=classify(p)
            damage_counts[p['damage_screening']['category']]+=1
            if p.get('identity_dossier',{}).get('conflicts'):
                exclusions['identity_conflicts']+=1
                continue
            if market_restricted(p):
                exclusions['export_or_registration_restriction']+=1
                continue
            if not amount_usable(p) or not p.get('make') or not p.get('model'):
                exclusions['price_or_family_missing']+=1
                continue
            if (not p['damage_screening']['eligible_for_opportunity_research'] if opportunity_profile else risky(p)):
                exclusions['damage_signal_or_unknown_damaged_scope']+=1
                continue
            if (type(p.get('year')) is not int or type(p.get('mileage_km')) is not int
                    or not normalized(p.get('fuel')) or not normalized(p.get('transmission'))):
                exclusions['year_mileage_fuel_or_gearbox_missing']+=1
                continue
            if not opportunity_profile or p['damage_screening']['category']=='clean':
                groups[(p['make'],p['model'],normalized(p.get('fuel')),normalized(p.get('transmission')))].append(p)
            eligible.append(p)
        bands = defaultdict(list)
        for p in eligible:
            if not 1000<=p['price_eur']<=20000:
                exclusions['purchase_outside_test_range']+=1
                continue
            try:
                location=published_location(p.get('city'))
            except ValueError:
                exclusions['nearby_location_unresolved']+=1
                continue
            training_pool=[q for q in groups[(p['make'],p['model'],normalized(p.get('fuel')),normalized(p.get('transmission'))) ]
                   if (q['source'],q['source_id'])!=(p['source'],p['source_id'])
                   and q.get('seller_type')==p.get('seller_type')
                   and same_variant(p,q)]
            peers=[q for q in training_pool if abs(q['year']-p['year'])<=policy['year_tolerance']
                   and abs(q['mileage_km']-p['mileage_km'])<=policy['mileage_tolerance_km']]
            # Mixed sources/reposts could be the same vehicle. Collapse identical
            # seller asking specifications/amounts before counting analogies.
            unique={}
            for q in peers:
                identity=(q['year'],q['mileage_km'],q.get('version_text') or q.get('trim'),q['price_eur'],q.get('city'))
                unique.setdefault(identity,q)
            peers=list(unique.values())
            if len(peers)<policy['minimum_comparables']:
                exclusions['fewer_than_'+str(policy['minimum_comparables'])+'_provisional_analogies']+=1
                continue
            prices=sorted(q['price_eur'] for q in peers)
            p25=prices[(len(prices)-1)//4]
            from .agents.market_prices import assess
            price_context=assess(p,training_pool,peers)
            learning.append(price_context)
            economics=feasibility(p,price_context)
            if opportunity_profile and not economics['passes_necessary_budget']:
                exclusions['cannot_cover_required_net_and_minimum_reserve_under_stress']+=1
                continue
            # Exploratory research ranks published asking prices without
            # imposing the strict profile's resale stress. Neither is net profit.
            stressed=price_context['asking_low_eur']*(100-policy['asking_stress_percent'])//100
            gap=stressed-p['price_eur']
            if gap<policy['minimum_headroom_eur'] or gap*100<p['price_eur']*policy['minimum_discount_percent']:
                exclusions['insufficient_gross_headroom']+=1
                continue
            band=min(3,p['price_eur']//5000)
            blockers=['total_purchase_price','identity_generation_engine_trim','damage_inspection',
                      'exact_variant_market_comparables','resale_value','all_operating_costs',
                      'supervisor_review','forecast_calibration']
            lead=dict(p,location=location,price_band=band,comparable_count=len(peers),
                market_price_agent=price_context,
                economic_screen=economics,
                damage_category=p['damage_screening']['category'],
                selection_rationale=dict(comparable_count=len(peers),confidence=price_context['confidence'],
                    remaining_cost_budget_eur=economics['remaining_cost_budget_eur'],
                    priority_score=economics['priority_score'],inspection_required=True),
                published_median_eur=round(median(prices)),published_p25_eur=p25,
                stressed_asking_reference_eur=stressed,gross_headroom_before_all_costs_eur=gap,
                net_margin_eur=None,buy_recommendation=False,status='research_priority',
                blocking_reasons=blockers,
                sources=[dict(source=q['source'],source_id=q['source_id'],observed_at=q['observed_at'],
                              url=q['url'],price_eur=q['price_eur']) for q in peers],
                next_tasks=['confirm_source_price_availability_and_specifications','inspect_damage',
                            'identify_required_operations_and_oem_parts','search_verified_compatible_all_in_offers',
                            'obtain_external_bodyshop_and_labor_quotes','rerun_conservative_economics'])
            from .margin_policy import minimum_net_margin_eur
            lead['minimum_required_net_margin_eur']=minimum_net_margin_eur(p['price_eur'])
            lead['net_margin_policy_status']='awaiting_verified_costs_and_resale_evidence'
            bands[band].append(lead)
        if opportunity_profile:
            mix_targets=quotas(limit)
            pools={category:{band:[] for band in range(4)} for category in MIX}
            for band,rows in bands.items():
                for lead in rows:pools[lead['damage_category']][band].append(lead)
            for categories in pools.values():
                for rows in categories.values():
                    rows.sort(key=lambda p:(-p['economic_screen']['priority_score'],-p['comparable_count'],p['source'],p['source_id']))
            selected=[];families=defaultdict(int);seen_targets=set()
            def target_identity(lead):
                return (lead['make'],lead['model'],lead['year'],lead['mileage_km'],lead['price_eur'],lead.get('version_text'),lead.get('city'))
            for category,target in mix_targets.items():
                count=0
                while count<target:
                    added=False
                    for band in range(4):
                        rows=pools[category][band]
                        rows.sort(key=lambda p:(families[(p['make'],p['model'])]>=2,-p['economic_screen']['priority_score'],-p['comparable_count']))
                        rows[:]=[p for p in rows if target_identity(p) not in seen_targets]
                        if rows and count<target:
                            lead=rows.pop(0);selected.append(lead);count+=1;added=True
                            seen_targets.add(target_identity(lead))
                            families[(lead['make'],lead['model'])]+=1
                    if not added:break
        else:
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
                    autonomous=autonomous,
                    price_bands=[dict(band=i,min_price_eur=max(1000,i*5000),
                        max_price_eur=20000 if i==3 else (i+1)*5000-1,
                        selected=sum(p['price_band']==i for p in selected)) for i in range(4)],
                    screening_policy=policy,
                    active_recent_observations=len(all_rows),projected_events=self.db.execute(
                        'SELECT count(*) FROM price_observations WHERE observed_at<=?',(as_of.isoformat(),)).fetchone()[0],
                    exclusions=dict(exclusions),candidates=selected,approved_buys=0,
                    severity_policy='non_severe_damage_mix_with_inspection_required' if opportunity_profile else 'damaged_or_damage_signal_excluded_from_first_test',
                    basis='published_asking_amounts_not_resale_or_net_profit',
                    repair_search='awaiting_verified_identity_and_required_parts',
                    price_memory_incremental=True)
        report['damage_mix']=dict(requested_percentages={k:round(v*100) for k,v in MIX.items()},
            enforced=opportunity_profile,available_observations=dict(damage_counts),
            categories=[dict(category=k,target=quotas(limit)[k],selected=sum(p['damage_category']==k for p in selected),
                shortage=max(0,quotas(limit)[k]-sum(p['damage_category']==k for p in selected))) for k in MIX],
            shortages_are_not_filled_with_unverified_or_severe_vehicles=True)
        dossiers=[p['identity_dossier'] for p in all_rows]
        report['identity_quality']=dict(version=dossiers[0]['version'] if dossiers else None,
            observations=len(dossiers),conflicted_observations=sum(bool(d['conflicts']) for d in dossiers),
            fields={field:dict(present=sum(d['fields'][field]['value'] is not None for d in dossiers),
                recovered_normalization_gaps=sum(d['fields'][field]['recovery_status']=='normalization_gap' for d in dossiers),
                conflicts=sum(d['fields'][field]['status']=='conflict' for d in dossiers)) for field in dossiers[0]['fields']} if dossiers else {},
            physical_identity_accuracy=None,verified_holdout_vehicles=0,
            verification_status='awaiting_independently_reviewed_vehicle_documents')
        from .margin_policy import policy as margin_policy
        validation=[p['validation']['median_absolute_error_eur'] for p in learning if p.get('validation')]
        report['net_margin_policy']=margin_policy()
        report['price_learning']=dict(evaluated_targets=len(learning),
            adjusted_contexts=sum(p['method']=='archive_trained_year_mileage' for p in learning),
            validated_contexts=len(validation),median_holdout_error_eur=round(median(validation)) if validation else None,
            asking_prices_only=True,completed_sale_prices_available=False)
        if self.pending(as_of):
            return dict(status='waiting_for_price_memory',run_id=run_id)
        with self.db:
            self.db.execute('INSERT INTO price_test_reports VALUES (?,?,?) ON CONFLICT(run_id) DO NOTHING',
                            (storage_run_id,as_of.isoformat(),self.db.json_param(canonical(report))))
        return report


def amount_usable(p):
    amount=p.get('price_eur')
    return (not market_restricted(p) and type(amount) is int and amount>0 and p.get('price_kind') not in ('deposit','installment')
            and not (p.get('price_kind')!='total' and PAYMENT.search(str(p.get('title') or '')+' '+str(p.get('description') or ''))))


def market_restricted(p):
    text=str(p.get('title') or '')+' '+str(p.get('description') or '')
    return bool(re.search(r'\bnon\s+immatricolabil\w*|\besclusivamente\s+per\s+esportazione|\besportazione\s+fuori\s+dall[’\x27]\s*unione\s+europea',text,re.I))


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
    if any(row.get('identity_dossier',{}).get('conflicts') for row in (p,q)):
        return False
    from .vehicle_identity import normalize_field
    for key in ('generation', 'trim', 'power_hp', 'power_kw', 'displacement_cc','engine_code','engine_name','drivetrain','body_type'):
        a, b = p.get(key), q.get(key)
        if a is not None and b is not None and normalize_field(key,a) != normalize_field(key,b):
            return False
    a, b = normalized(p.get('version_text')), normalized(q.get('version_text'))
    if a or b:
        if a and b and a == b:
            return True
        if not all(type(p.get(key)) is int and p[key] == q.get(key)
                   for key in ('power_hp','displacement_cc')):
            return False
        # Headline prefixes may repeat the model/generation/year. Only remove
        # these editorial prefixes once matching published engine facts exist;
        # retain trim, engine names, drive type, doors and sport labels.
        def signature(row):
            value=normalized(row.get('version_text'))
            if not value:return None
            model=normalized(row.get('model'))
            if model:
                value=re.sub(r'^(?:'+re.escape(model)+r'\s+)+','',value)
            generation=re.match(r'^(i|ii|iii|iv|v|vi|vii|viii)\b',value)
            value=re.sub(r'^(?:i|ii|iii|iv|v|vi|vii|viii)\b(?:\s+(?:19|20)\d{2})?\s*','',value)
            if type(row.get('year')) is int:
                value=re.sub(r'\s+'+str(row['year'])+r'$','',value)
            return value.strip(), generation.group(1) if generation else None
        a,b=signature(p),signature(q)
        if not a or not b or (a[1] and b[1] and a[1]!=b[1]):
            return False
        return bool(a[0] and a[0]==b[0])
    return all(normalized(p.get(key)) and normalized(p.get(key)) == normalized(q.get(key))
               for key in ('generation', 'trim'))



class BackgroundPriceMemory:
    def __init__(self,path):
        self.path=path
        self.ready=False
        self.failures=0
        self.last_poll=None
        self.max_batch_size=max(1,min(250,int(os.getenv('DEAL_FINDER_PRICE_PROJECTION_MAX_BATCH','100'))))
        self.batch_size=min(self.max_batch_size,max(1,int(os.getenv('DEAL_FINDER_PRICE_PROJECTION_BATCH','25'))))
        self.last_report=None
        self.retry_at=None
        self.sync_cursor=None

    def step(self, run_id=None):
        import time
        started=time.monotonic()
        if self.retry_at is not None and started<self.retry_at:
            return None
        discovery=research_policy()['profile']=='discovery'
        if not discovery and self.last_poll is not None and self.ready and time.monotonic()-self.last_poll<60:
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
            discovery=research_policy()['profile']=='discovery'
            if discovery:
                count=memory.sync(now,limit=self.batch_size,after=self.sync_cursor,recover_identity=False)
            else:
                count=memory.sync(now,limit=self.batch_size,after=self.sync_cursor)
            self.sync_cursor=memory.next_cursor
            projection_seconds=time.monotonic()-projection_started
            stage='check_completion'
            # Broad discovery can use the compact observations already available.
            # Strict comparisons still wait for complete source identity recovery.
            self.ready=discovery or (count<self.batch_size and not memory.pending(now))
            out=dict(price_memory='ready' if self.ready else 'building',projected_this_step=count)
            automatic=autonomous_enabled()
            report_due=not discovery or self.last_report is None or started-self.last_report>=60
            if self.ready and (run_id or automatic) and report_due:
                run_id=run_id or 'archive-continuous'
                if automatic:
                    run_id=memory.automatic_run_id(run_id,now)
                stage='compare_archive'
                report=memory.first_test(run_id,now,autonomous=automatic)
                if report['status']=='waiting_for_price_memory':
                    self.ready=False
                    return dict(price_memory='building',projected_this_step=count)
                self.last_report=time.monotonic()
                stage='enqueue_verifications'
                if report.get('candidates'):
                    from .queue import Queue
                    from .agents.enrichment import listing_input
                    queue=Queue(self.path)
                    try:
                        batch=priority_batch_id(run_id)
                        if automatic or not queue.db.execute('SELECT 1 FROM batches WHERE batch_id=?',(batch,)).fetchone():
                            records=[]
                            for p in report['candidates']:
                                observation_batch=priority_observation_batch(p) if automatic else batch
                                if automatic and queue.db.execute('SELECT 1 FROM batches WHERE batch_id=?',(observation_batch,)).fetchone():
                                    continue
                                original=archive.db.execute('SELECT url,payload FROM listing_events WHERE source=? AND source_id=? AND observed_at=?',
                                    (p['source'],p['source_id'],p['observed_at'])).fetchone()
                                record=dict(task='archive_enrichment',listing=listing_input(p['source'],p['source_id'],p['observed_at'],
                                    original[0],archive.db.json_decode(original[1])))
                                if automatic:
                                    queue.submit(observation_batch,[record])
                                else:
                                    records.append(record)
                            if records:
                                queue.submit(batch,records)
                    finally:
                        queue.close()
                out.update(first_test=report['status'],run_id=run_id,candidates=len(report.get('candidates',[])),
                           autonomous=automatic,price_bands=report.get('price_bands'),
                           approved_buys=report.get('approved_buys',0))
            self.failures=0
            self.retry_at=None
            # A successful slow query is still too much work for this database.
            # Stay comfortably below the statement limit instead of doubling
            # every success back to the same batch that just timed out.
            if projection_seconds>5:
                self.batch_size=max(1,self.batch_size//2)
            elif projection_seconds<2:
                self.batch_size=min(self.max_batch_size,self.batch_size*2)
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
