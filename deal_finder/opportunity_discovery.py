"""Broad lead collection. Approximate asking signals never approve a purchase."""
from collections import defaultdict
from statistics import mean
from .damage_screening import classify,signals


def damage_group(p):
    if p.get('_discovery_group'):return p['_discovery_group']
    category=(p.get('_discovery_damage') or classify(p))['category']
    if category!='unknown':return category
    source=p.get('damage_source_claims') or {}
    disclosed=(p.get('condition')=='damaged' or any(source.get(k) is True for k in ('hadAccident','isCurrentlyDamaged'))
        or (source.get('rawData.condition.damage') or {}).get('isCurrentlyDamaged') is True
        or signals(str(p.get('title') or '')+' '+str(p.get('description') or ''),r'\b(?:incidentat\w*|sinistrat\w*|danneggiat\w*)\b'))
    return 'damaged_unspecified' if disclosed else 'condition_unknown'


def identity_present(p):
    return bool(p.get('make') and p.get('model') and type(p.get('year')) is int
                and 1980 <= p['year'] <= 2030 and type(p.get('mileage_km')) is int
                and 0 <= p['mileage_km'] <= 1000000)


class PeerIndex:
    """Index exact condition cohorts and nearby year/mileage bins for broad discovery."""
    def __init__(self,rows):
        from .price_memory import amount_usable,normalized
        self.bins=defaultdict(list)
        for row in rows:
            if not identity_present(row) or not amount_usable(row):continue
            group=damage_group(row)
            if group=='severe':continue
            row['_discovery_fuel']=normalized(row.get('fuel'))
            row['_discovery_transmission']=normalized(row.get('transmission'))
            key=(normalized(row['make']),normalized(row['model']),group,row['year'],row['mileage_km']//10000)
            self.bins[key].append(row)

    def candidates(self,target):
        from .price_memory import normalized
        family=(normalized(target['make']),normalized(target['model']),damage_group(target))
        for year in range(target['year']-3,target['year']+4):
            for bucket in range(max(0,target['mileage_km']-60000)//10000,(target['mileage_km']+60000)//10000+1):
                yield from self.bins.get((*family,year,bucket),())


def peers_for(target, rows):
    from .price_memory import amount_usable, normalized
    unique={};indexed=isinstance(rows,PeerIndex)
    targets={key:normalized(target.get(key)) for key in ('make','model','fuel','transmission')}
    group=damage_group(target)
    for q in rows.candidates(target) if indexed else rows:
        if not indexed:
            if not identity_present(q) or not amount_usable(q):continue
            if any(normalized(q.get(k)) != targets[k] for k in ('make','model')):continue
            if damage_group(q)!=group or group=='severe':continue
        if (q.get('source'),q.get('source_id')) == (target.get('source'),target.get('source_id')):continue
        if abs(q['year']-target['year'])>3 or abs(q['mileage_km']-target['mileage_km'])>60000:continue
        incompatible=False
        for key in ('fuel','transmission'):
            value=q.get('_discovery_'+key) if indexed else normalized(q.get(key))
            if value and targets[key] and value!=targets[key]:incompatible=True;break
        if incompatible:continue
        key=(q['year'],q['mileage_km'],q['price_eur'],q.get('city'))
        unique.setdefault(key,q)
    return list(unique.values())


def signal(target, rows):
    peers=peers_for(target,rows)
    if len(peers)<2:return None
    reference=round(mean(q['price_eur'] for q in peers))
    gap=reference-target['price_eur']
    from .margin_policy import minimum_net_margin_eur
    required=minimum_net_margin_eur(target['price_eur'])
    from .conservative_prices import screen
    conservative = screen(target, peers)
    # All compatible prices contribute to the mean; keep a bounded audit sample.
    evidence=peers
    if len(peers)>50:
        sample=[min(peers,key=lambda q:q['price_eur'])]+[peers[round(i*(len(peers)-1)/49)] for i in range(1,49)]+[max(peers,key=lambda q:q['price_eur'])]
        evidence=list({(q['source'],q['source_id']):q for q in sample}.values())
        evidence.sort(key=lambda q:q['price_eur'])
    return dict(method='broad_model_year_mileage_asking_mean',confidence='low',
                comparable_count=len(peers),asking_typical_eur=reference,
                asking_low_eur=min(q['price_eur'] for q in peers),
                gross_headroom_before_all_costs_eur=gap,
                minimum_required_asking_discount_eur=required,
                damage_comparison_group=damage_group(target),damaged_and_healthy_compared_directly=False,
                apparent_opportunity=gap>=required,
                conservative_price_screen=conservative,
                exact_variant_comparison=False,net_margin_eur=None,buy_recommendation=False,
                source_evidence_is_sample=len(peers)>50,source_evidence_count=len(evidence),
                unresolved_factors=['exact_engine_generation_trim','damage','resale','all_costs'],
                sources=[dict(source=q['source'],source_id=q['source_id'],observed_at=q['observed_at'],
                              url=q.get('url'),price_eur=q['price_eur'],year=q['year'],mileage_km=q['mileage_km']) for q in evidence])


def build_report(rows,run_id,as_of,version,policy,limit,autonomous):
    from .price_memory import amount_usable,normalized
    from .margin_policy import policy as net_policy,minimum_net_margin_eur
    rows=[dict(p,_discovery_damage=classify(p)) for p in rows]
    for p in rows:p['_discovery_group']=damage_group(p)
    peers=PeerIndex(rows)
    exclusions=defaultdict(int);leads=[];seen=set()
    for p in rows:
        if not identity_present(p) or not amount_usable(p):
            exclusions['missing_basic_identity_or_total_price']+=1;continue
        if not 1000<=p['price_eur']<=20000:
            exclusions['purchase_outside_test_range']+=1;continue
        damage=p['_discovery_damage']
        if damage['category']=='severe':
            exclusions['known_severe_damage']+=1;continue
        context=signal(p,peers)
        if context is None:
            exclusions['fewer_than_two_broad_comparables']+=1;continue
        if not context['apparent_opportunity']:
            exclusions['no_initial_price_signal']+=1;continue
        key=(p['make'],p['model'],p['year'],p['mileage_km'],p['price_eur'],p.get('city'))
        if key in seen:continue
        seen.add(key)
        flags=['approximate_identity','exact_variant_comparison_pending','all_costs_and_resale_unverified']
        if p.get('identity_dossier',{}).get('conflicts'):flags.append('identity_conflicts_to_review')
        if damage['category']=='unknown':flags.append('condition_unknown')
        if not p.get('city'):flags.append('location_to_verify')
        public={k:v for k,v in p.items() if not k.startswith('_discovery_')}
        leads.append(dict(public,status='discovery_lead',screening_stage='broad_discovery',
            damage_category=damage['category'],price_band=min(3,p['price_eur']//5000),
            discovery_context=context,market_price_agent=context,comparable_count=context['comparable_count'],
            price_priority_passed=context['conservative_price_screen']['price_priority_passed'],
            conservative_price_screen=context['conservative_price_screen'],
            sources=context['sources'],gross_headroom_before_all_costs_eur=context['gross_headroom_before_all_costs_eur'],
            uncertainty_flags=flags,blocking_reasons=flags,minimum_required_net_margin_eur=minimum_net_margin_eur(p['price_eur']),
            net_margin_eur=None,buy_recommendation=False,next_tasks=['verify_identity_and_damage','rerun_conservative_economics']))
    leads.sort(key=lambda p:(not p['price_priority_passed'],-p['gross_headroom_before_all_costs_eur']/p['price_eur'],-p['comparable_count'],p['source'],p['source_id']))
    return dict(run_id=run_id,as_of=as_of.isoformat(),screening_version=version,status='completed_research_test',
        autonomous=autonomous,screening_stage='broad_discovery',screening_policy=policy,
        active_recent_observations=len(rows),exclusions=dict(exclusions),candidates=leads[:limit],
        total_discovery_leads=len(leads),returned_limit=limit,approved_buys=0,
        total_price_priority_leads=sum(p['price_priority_passed'] for p in leads),
        returned_price_priority_leads=sum(p['price_priority_passed'] for p in leads[:limit]),
        net_margin_policy=net_policy(),final_conservative_filter_required=True,
        basis='approximate_asking_signal_not_resale_or_net_profit',
        severity_policy='known_severe_excluded_unknown_condition_allowed_for_review',
        damage_mix=dict(enforced=False),
        price_bands=[dict(band=i,min_price_eur=max(1000,i*5000),max_price_eur=20000 if i==3 else (i+1)*5000-1,
            selected=sum(p['price_band']==i for p in leads[:limit])) for i in range(4)])
