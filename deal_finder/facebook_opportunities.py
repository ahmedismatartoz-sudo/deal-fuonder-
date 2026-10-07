"""Autonomous Facebook archive review: low-price screening is not net profit.

Scans every latest Facebook snapshot, compares against the whole price memory,
and keeps the economic shortlist distinct from a capped evidence work queue.
No collection/provider is called here. Paid photo work uses the existing queue
and a bounded, idempotent opt-in on the already configured worker.
"""
import hashlib
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from .archive import canonical
from .facebook_evidence import recover
from .margin_policy import minimum_net_margin_eur, policy
from .vehicle_identity import normalize_field
from .damage_screening import classify

VERSION = 'facebook-top50-evidence-v2'
SOURCE = 'facebook_marketplace'
PREFIX = 'facebook-finalists-v1-'


def norm(value):
    return ' '.join(str(value or '').casefold().split())


def family(row):
    make,model = norm(row.get('make')),norm(row.get('model'))
    if make == 'bmw':
        match=re.fullmatch(r'([1-8])\d{2}[a-z]*',model)
        if match: model='serie '+match[1]
    if make == 'mercedes-benz':
        match=re.fullmatch(r'([abc es])\s*\d{3}(?:\s.*)?',model)
        if match: model='classe '+match[1]
    for base in ('golf','polo','clio','fiesta','focus','208','308'):
        if model.startswith(base+' '): model=base; break
    return make,model


def transmission(value):
    value=normalize_field('transmission',value)
    return 'automatic' if value == 'semi_automatic' else value


def conditional_price(row):
    text=norm(str(row.get('title') or '')+' '+str(row.get('description') or ''))
    return bool(re.search(r'(?:prezzo.{0,40}(?:solo con|con obbligo|vincolato|soggetto).{0,25}finanziament|finanziamento obbligatorio|prezzo.{0,25}con finanziament|iva\s*esclusa|\+\s*iva|(?:solo|per)\s*ricambi|fermo amministrativo|senza documenti)',text))


def acquisition_service(row):
    text=norm(str(row.get('description') or ''))
    return bool(re.search(r'\b(?:ritiriamo|compriamo|acquistiamo)\s+(?:le\s+)?auto\s+usate\b',text)
                and re.search(r'\b(?:qualsiasi tipo|se vuoi vendere|valutazioni|pagamento rapido)\b',text))


def instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def variant(row):
    text = norm(str(row.get('title') or '')+' '+str(row.get('model') or '')+' '+str(row.get('version_text') or ''))
    performance = tuple(sorted(set(re.findall(r'\b(?:gti|gtd|abarth|amg|jcw|rs|type r|cooper s|gr|st)\b',text))))
    drive = 'awd' if re.search(r'\b(?:4x4|4wd|quattro|xdrive|4matic|allgrip|awd)\b',text) else row.get('drivetrain')
    body = ('wagon' if re.search(r'\b(?:sw|touring|avant|wagon|variant)\b',text) else
            'convertible' if re.search(r'\b(?:cabrio|convertible)\b',text) else row.get('body_type'))
    litres = re.search(r'(?<![\d.])([1-6][.,]\d)(?!\d)',text)
    return performance, drive, body, float(litres[1].replace(',','.')) if litres else None


def compatible(target, peer):
    if (peer['source'],peer['source_id']) == (target['source'],target['source_id']):
        return False
    if family(target) != family(peer):
        return False
    if any(type(row.get(k)) is not int for row in (target,peer) for k in ('year','mileage_km')):
        return False
    if abs(target['year']-peer['year']) > 1 or abs(target['mileage_km']-peer['mileage_km']) > 20000:
        return False
    for k in ('fuel','transmission','generation'):
        a,b = ((transmission(target.get(k)),transmission(peer.get(k))) if k == 'transmission'
               else (normalize_field(k,target.get(k)),normalize_field(k,peer.get(k))))
        if a is not None and b is not None and a != b:
            return False
    for k in ('displacement_cc','power_hp'):
        a,b = normalize_field(k,target.get(k)),normalize_field(k,peer.get(k))
        if a and b and abs(a-b) > (50 if k == 'displacement_cc' else max(3,min(a,b)*.1)):
            return False
    a,b = variant(target),variant(peer)
    if a[0] != b[0]:
        return False
    for x,y in zip(a[1:3],b[1:3]):
        if x and y and norm(x) != norm(y):
            return False
    # A known premium drive or body cannot use a peer missing that evidence.
    if a[1] == 'awd' and b[1] != 'awd':
        return False
    if a[2] in ('wagon','convertible') and b[2] != a[2]:
        return False
    if a[3] and b[3] and abs(a[3]-b[3]) > .05:
        return False
    return True


def match_complete(target, peers):
    for p in [target,*peers]:
        if not p.get('fuel') or not p.get('transmission'):
            return False
    for p in peers:
        # At least one observed engine discriminator on both sides.
        a,b = variant(target)[3],variant(p)[3]
        engine = bool(a and b and abs(a-b) <= .05)
        for k in ('displacement_cc','power_hp'):
            x,y = target.get(k),p.get(k)
            if x and y and abs(x-y) <= (50 if k == 'displacement_cc' else max(3,min(x,y)*.1)):
                engine = True
        if not engine:
            return False
    return True


def build_report(facebook, market, as_of, *, reviews=None, limit=50):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Limit must be 1..50')
    reviews = reviews or {}
    families = defaultdict(list)
    peer_exclusions = Counter()
    for p in market:
        seen = instant(p.get('observed_at'))
        if not p.get('active', True) or seen is None or not timedelta(0) <= as_of-seen <= timedelta(days=30):
            peer_exclusions['inactive_stale_or_future'] += 1; continue
        if p.get('price_kind') != 'total' or type(p.get('price_eur')) is not int or p['price_eur'] < 1000:
            peer_exclusions['price_not_total'] += 1; continue
        from .price_memory import market_restricted, amount_usable
        if not amount_usable(p) or market_restricted(p) or conditional_price(p) or p.get('identity_dossier',{}).get('conflicts'):
            peer_exclusions['identity_or_price_terms'] += 1; continue
        damage = classify(p)['category']
        if damage in ('severe','non_severe','minimal') or p.get('condition') == 'damaged':
            peer_exclusions['disclosed_damage'] += 1; continue
        families[family(p)].append(p)
    candidates, opportunities, exclusions = [], [], Counter()
    profile = Counter()
    for p in facebook:
        for k in ('year','mileage_km','fuel','transmission','make','model'):
            profile[k] += p.get(k) is not None
        seen = instant(p.get('observed_at'))
        if not p.get('active',True) or seen is None or not timedelta(0) <= as_of-seen <= timedelta(days=30):
            exclusions['inactive_stale_or_future'] += 1; continue
        price = p.get('price_eur')
        if acquisition_service(p):
            exclusions['vehicle_purchase_service_not_sale'] += 1; continue
        if type(price) is not int or not 1000 <= price <= 20000:
            exclusions['outside_purchase_budget'] += 1; continue
        from .price_memory import amount_usable
        if not amount_usable(p) or conditional_price(p):
            exclusions['conditional_or_unusable_price'] += 1; continue
        damage = classify(p)['category']
        evidence = p.get('facebook_evidence') or {}
        if evidence.get('conflicts') or p.get('identity_dossier',{}).get('conflicts'):
            exclusions['identity_conflict'] += 1; continue
        if damage == 'severe':
            exclusions['known_severe_damage'] += 1; continue
        mileage_options=[p['mileage_km']] if type(p.get('mileage_km')) is int else []
        raw_miles=evidence.get('raw_provider_car_miles')
        if not mileage_options and type(raw_miles) in (int,float) and 0<=raw_miles<=1000000:
            # Both possible units remain hypotheses. Neither becomes fact.
            mileage_options=sorted({int(raw_miles),int(raw_miles*1.609344)})
        if any(p.get(k) is None for k in ('make','model','year')) or not mileage_options:
            exclusions['base_identity_or_explicit_km_missing'] += 1; continue
        peers = [q for q in families[family(p)]
                 if any(compatible(dict(p,mileage_km=km),q) for km in mileage_options)]
        # Conservatively collapse indistinguishable reposts; don't claim that
        # distinct source IDs alone prove independent physical vehicles.
        distinct = {}
        for q in sorted(peers,key=lambda r:(r['price_eur'],r['source'],r['source_id'])):
            key = (q['year'],q['mileage_km'],q['price_eur'],norm(q.get('city')),norm(q.get('version_text')))
            distinct.setdefault(key,q)
        peers = list(distinct.values())
        if len(peers) < 3:
            exclusions['fewer_than_three_close_comparables'] += 1; continue
        low = min(q['price_eur'] for q in peers)
        sale = low * 85 // 100
        # The fixed reserve is not a repair quote. This is ONLY an upper
        # bound for rejecting hopeless candidates before unknown costs.
        ceiling = sale-price-750
        required = minimum_net_margin_eur(price)
        if ceiling < required or (low-price)*100 < low*20:
            exclusions['insufficient_pessimistic_headroom'] += 1; continue
        blockers = ['purchase_cash_price_and_availability_to_confirm',
                    'condition_and_hidden_damage_to_inspect','repair_and_operating_cost_bounds_missing']
        if p.get('mileage_km') is None:
            blockers.append('provider_mileage_units_unresolved')
        if not match_complete(p,peers):
            blockers.append('engine_or_variant_comparison_incomplete')
        if not evidence.get('registration_year_verified'):
            blockers.append('seller_title_year_not_document_verified')
        if any(classify(q)['category'] != 'clean' for q in peers):
            blockers.append('comparable_condition_not_confirmed')
        if damage != 'clean':
            blockers.append('repaired_history_resale_requires_adverse_review')
        review = reviews.get((p['source_id'],p['observed_at'])) or {}
        photo = review.get('enrichment',{}).get('photo_damage_assessment') or {}
        if photo.get('possible_severe_damage'):
            exclusions['possible_severe_damage_in_photos'] += 1; continue
        economics = review.get('independent_review',{}).get('economics') or {}
        net = None
        # Only the established independent all-cost review can provide a net
        # figure. A photo identification or untrusted seller cost cannot.
        if review.get('independent_review',{}).get('approved_for_final_checks') is True:
            computed = economics.get('margin_low_cents')
            if type(computed) is int and computed >= required*100:
                net = computed//100
                blockers = []
        public = {k:p.get(k) for k in ('source','source_id','observed_at','url','title','description',
                  'make','model','year','mileage_km','fuel','transmission','city','province','price_eur','price_kind')}
        public.update(status='evidence_complete_margin_estimate' if net is not None else 'research_candidate',
            price_band=min(3,price//5000),damage_category=damage,
            minimum_required_net_margin_eur=required,lowest_comparable_asking_eur=low,
            conservative_asking_exit_scenario_eur=sale,
            maximum_margin_before_unknown_costs_eur=ceiling,net_margin_estimate_eur=net,
            reserve_eur=750,repair_costs_unknown=net is None,blocking_reasons=blockers,
            buy_recommendation=False,sale_speed_calibrated=False,
            research_mileage_hypotheses_km=mileage_options,
            photo_review=photo or None,photo_count=len(p.get('image_urls') or []),
            comparable_count=len(peers),source_evidence_complete=len(peers)<=25,
            comparables=[{k:q.get(k) for k in ('source','source_id','observed_at','url','price_eur',
                         'year','mileage_km','fuel','transmission','version_text')} for q in peers[:25]])
        candidates.append(public)
        if net is not None:
            opportunities.append(public)
    candidates.sort(key=lambda p:(-p['maximum_margin_before_unknown_costs_eur'],-p['comparable_count'],p['source_id']))
    opportunities.sort(key=lambda p:(-p['net_margin_estimate_eur'],p['source_id']))
    research_statuses=Counter()
    research_blocks=Counter()
    for p in facebook:
        research=reviews.get((p['source_id'],p['observed_at']),{}).get('enrichment',{}).get('identity_research') or {}
        if research:
            research_statuses[research.get('status','unknown')]+=1
            reason=research.get('reason')
            if reason:
                research_blocks[str(reason)]+=1
    return dict(screening_version=VERSION,as_of=as_of.isoformat(),autonomous=True,
        screening_policy=dict(profile='facebook_finalists',comparison_year_tolerance=1,
            comparison_mileage_tolerance_km=20000,minimum_comparables=3,sale_stress_percent=15,
            minimum_discount_percent=20,all_costs_required=True),
        status='completed_archive_screen',requested_opportunities=limit,
        facebook_ads_examined=len(facebook),market_rows_examined=len(market),
        recovered_field_counts=dict(profile),exclusions=dict(exclusions),peer_exclusions=dict(peer_exclusions),
        research_execution_statuses=dict(research_statuses),research_blocking_reasons=dict(research_blocks),
        net_margin_policy=policy(),total_research_candidates=len(candidates),
        candidates=candidates[:limit],opportunities=opportunities[:limit],
        qualifying_opportunities=len(opportunities),returned_opportunities=min(limit,len(opportunities)),
        approved_buys=0,shortage=max(0,limit-len(opportunities)),
        research_queue_is_not_valid_opportunities=True,
        basis='lowest_compatible_asking_less_15_percent_with_unknown_costs_unresolved')


class FacebookScreening:
    def __init__(self, path):
        self.path=path
        self.last_poll=None
        self.last_revision=None
        self.failures=0
        self.projection_cursor=None

    def step(self):
        if self.last_poll is not None and time.monotonic()-self.last_poll < 60:
            return None
        self.last_poll=time.monotonic()
        from .archive import Archive
        from .price_memory import PriceMemory
        from .queue import Queue
        archive=queue=None
        try:
            archive=Archive(self.path)
            memory=PriceMemory(archive.db)
            now=datetime.now(timezone.utc)
            revision=archive.db.execute('SELECT count(*),max(observed_at) FROM listing_events WHERE source=? AND observed_at<=?',
                                        (SOURCE,now.isoformat())).fetchone()
            price_revision=archive.db.execute('SELECT count(*),max(observed_at) FROM price_observations WHERE observed_at<=?',
                                              (now.isoformat(),)).fetchone()
            queue=Queue(self.path)
            source_expr=("r.payload#>>'{listing,source}'" if queue.db.dialect=='postgres'
                         else "json_extract(r.payload,'$.listing.source')")
            finished=queue.db.execute('''SELECT count(*) FROM agent_runs a JOIN jobs j ON j.id=a.job_id
                JOIN raw_records r ON r.id=j.raw_id WHERE r.batch_id LIKE ? OR '''+source_expr+'''=?''',
                (PREFIX+'%',SOURCE)).fetchone()[0]
            budget=max(0,min(50,int(os.getenv('DEAL_FINDER_FACEBOOK_RESEARCH_LIMIT','0'))))
            revision=(tuple(revision),tuple(price_revision),finished,budget)
            digest=hashlib.sha256(canonical(revision).encode()).hexdigest()[:20]
            run_id=VERSION+'-'+now.date().isoformat()+'-'+digest
            if revision == self.last_revision:
                return None
            saved=archive.db.execute('SELECT 1 FROM price_test_reports WHERE run_id=?',(run_id,)).fetchone()
            if saved:
                self.last_revision=revision
                return dict(facebook_screening='already_completed',run_id=run_id,projected_this_step=0)
            # Check the persisted archive/review fingerprint before projection.
            # An unchanged, fully reviewed archive must not scan all original
            # observations repeatedly just to discover that nothing is missing.
            projected=memory.sync(now,limit=100,after=self.projection_cursor,recover_identity=False)
            self.projection_cursor=memory.next_cursor
            facebook=[]
            cursor=''
            while True:
                rows=archive.db.execute('''SELECT e.source_id,e.observed_at,e.url,e.active,e.payload
                    FROM listing_events e WHERE e.source=? AND e.source_id>? AND e.observed_at<=?
                    AND NOT EXISTS(SELECT 1 FROM listing_events n WHERE n.source=e.source
                        AND n.source_id=e.source_id AND n.observed_at>e.observed_at AND n.observed_at<=?)
                    ORDER BY e.source_id LIMIT 50''',(SOURCE,cursor,now.isoformat(),now.isoformat())).fetchall()
                for identifier,observed,url,active,payload in rows:
                    cursor=identifier
                    p=recover(archive.db.json_decode(payload),source_url=url)
                    p.pop('original',None)
                    p['identity_dossier']={'conflicts':p['identity_dossier']['conflicts']}
                    p.update(source=SOURCE,source_id=identifier,observed_at=observed,url=url,active=bool(active))
                    facebook.append(p)
                if len(rows)<50:
                    break
            market=[]
            for p in memory.current(now,recover_identity=False):
                if p['source'] != SOURCE:
                    p['identity_dossier']={'conflicts':p.get('identity_dossier',{}).get('conflicts',[])}
                    market.append(p)
            reviews={}
            review_expr=("a.outputs->'independent_review' IS NOT NULL" if queue.db.dialect=='postgres'
                         else "json_type(a.outputs,'$.independent_review') IS NOT NULL")
            results=queue.db.execute('''SELECT r.payload,a.outputs FROM raw_records r
                JOIN jobs j ON j.raw_id=r.id JOIN agent_runs a ON a.job_id=j.id
                WHERE (r.batch_id LIKE ? OR ('''+source_expr+'''=? AND '''+review_expr+'''))
                AND NOT EXISTS(SELECT 1 FROM agent_runs n
                    WHERE n.job_id=a.job_id AND n.attempt>a.attempt) ORDER BY a.as_of,a.id''',
                (PREFIX+'%',SOURCE)).fetchall()
            for raw,output in results:
                listing=queue.db.json_decode(raw).get('listing',{})
                key=(listing.get('source_id'),listing.get('observed_at'))
                reviews.setdefault(key,{}).update(queue.db.json_decode(output))
            for p in facebook:
                output=reviews.get((p['source_id'],p['observed_at']),{})
                research=output.get('enrichment',{}).get('identity_research') or {}
                p['facebook_evidence']['conflicts'].extend(research.get('conflicting_fields') or [])
                claims=research.get('accepted_claims') or []
                for field in ('fuel','transmission','mileage_km','generation','body_type'):
                    observations={c['value'] for c in claims if c.get('field')==field
                        and c.get('origin') in (('document','photo') if field=='mileage_km' else ('document',))}
                    if len(observations)!=1 or p.get(field) is not None:
                        continue
                    value=next(iter(observations))
                    if field=='mileage_km':
                        value=int(value) if value.isdigit() else None
                    else:
                        value=normalize_field(field,value)
                    if value is not None:
                        p[field]=value
                        p['facebook_evidence']['claims'][field]=dict(value=value,origin='persisted_vehicle_evidence_reading',verified=False)
            report=build_report(facebook,market,now,reviews=reviews)
            report['run_id']=run_id
            # At most 50 source observations may consume existing opted-in
            # enrichment calls for this campaign. Restarts never repeat them.
            budget=max(0,min(50,int(os.getenv('DEAL_FINDER_FACEBOOK_RESEARCH_LIMIT','0'))))
            used=queue.db.execute('SELECT count(*) FROM batches WHERE batch_id LIKE ?',(PREFIX+'%',)).fetchone()[0]
            from .agents.enrichment import listing_input
            for candidate in report['candidates']:
                key=canonical([SOURCE,candidate['source_id'],candidate['observed_at']])
                batch=PREFIX+hashlib.sha256(key.encode()).hexdigest()[:24]
                if queue.db.execute('SELECT 1 FROM batches WHERE batch_id=?',(batch,)).fetchone():
                    continue
                if used >= budget:
                    break
                p=archive.db.execute('SELECT payload FROM listing_events WHERE source=? AND source_id=? AND observed_at=?',
                                     (SOURCE,candidate['source_id'],candidate['observed_at'])).fetchone()
                queue.submit(batch,[dict(task='archive_enrichment',listing=listing_input(SOURCE,
                    candidate['source_id'],candidate['observed_at'],candidate['url'],archive.db.json_decode(p[0])))])
                used+=1
            report['research_observation_budget']=budget
            report['research_observations_queued_total']=used
            with archive.db:
                archive.db.execute('INSERT INTO price_test_reports VALUES (?,?,?) ON CONFLICT(run_id) DO NOTHING',
                    (run_id,now.isoformat(),archive.db.json_param(canonical(report))))
            self.last_revision=revision
            self.failures=0
            return dict(facebook_screening='completed',run_id=run_id,facebook_ads_examined=len(facebook),
                research_candidates=report['total_research_candidates'],research_observations_queued_total=used,
                qualifying_opportunities=report['qualifying_opportunities'],projected_this_step=projected)
        except Exception as error:
            self.failures+=1
            return dict(facebook_screening='retry_later',error_type=type(error).__name__,attempts=self.failures)
        finally:
            if queue: queue.close()
            if archive: archive.close()
