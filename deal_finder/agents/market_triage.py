"""Coarse, provisional family prices for enrichment priority, never valuation."""
from statistics import median
from ..models import normalize


def review(listing, db, as_of):
    output = dict(status='needs_evidence', basis='published_family_amounts_not_verified_total_prices',
                  candidate_for_verification=False, priority_enrichment=False,
                  exact_variant_verified=False, calibrated=False, sources=[],
                  observed_envelope_eur=None, potential_before_repairs_eur=None)
    try:
        make, model = normalize(listing['make']), normalize(listing['model'])
    except (ValueError, KeyError, TypeError):
        return dict(output, reason='Make and model family required for sourced analogies')
    price = listing.get('price_eur')
    if type(price) is not int or price <= 0:
        return dict(output, reason='Published acquisition amount unresolved')
    from ..price_memory import PriceMemory, amount_usable, risky, same_variant, research_policy
    policy = research_policy()
    from ..damage_screening import classify,feasibility
    if policy['profile']=='discovery':
        from ..opportunity_discovery import signal,identity_present
        if not identity_present(listing) or not amount_usable(listing) or classify(listing)['category']=='severe':
            return dict(output,reason='Basic identity, usable price and no known severe damage required')
        memory=PriceMemory(db)
        context=signal(listing,list(memory.current(as_of,make=make,model=model)))
        if context is None:return dict(output,reason='Two broad reference vehicles required')
        return dict(output,status='broad_discovery_context',market_price_agent=context,
                    priority_enrichment=context['apparent_opportunity'],candidate_for_verification=context['apparent_opportunity'],
                    sources=context['sources'],exact_variant_verified=False,final_conservative_filter_required=True)
    if listing.get('identity_dossier',{}).get('conflicts'):
        return dict(output,reason='Open identity contradictions require review')
    opportunity_profile=policy['profile']=='opportunities'
    if opportunity_profile and not classify(listing)['eligible_for_opportunity_research']:
        return dict(output,reason='Severe or unspecified damage requires review')
    memory = PriceMemory(db)
    # Development callers may ingest directly; deployed worker builds memory
    # before consuming jobs. Read only new raw observations, never whole families.
    while memory.sync(as_of):
        pass
    amounts, sources, cohort, nearby = [], [], [], []
    for p in memory.current(as_of, make=make, model=model, source=listing['source']):
        source_id, amount = p['source_id'], p.get('price_eur')
        if source_id == listing['source_id'] or not amount_usable(p) or risky(p) or not same_variant(listing,p):
            continue
        if p.get('seller_type') != listing.get('seller_type'):
            continue
        if (not isinstance(listing.get('transmission'), str)
                or not isinstance(p.get('transmission'), str)
                or not listing['transmission'].strip() or not p['transmission'].strip()
                or normalize(listing['transmission']) != normalize(p['transmission'])):
            continue
        if any(type(p.get(key)) is not int or type(listing.get(key)) is not int
               for key in ('year','mileage_km')):
            continue
        fuel, condition = p.get('fuel'), p.get('condition')
        if (isinstance(listing.get('fuel'), str) and isinstance(fuel, str)
                and listing['fuel'].strip() and fuel.strip() and normalize(listing['fuel']) != normalize(fuel)):
            continue
        if opportunity_profile and classify(p)['category']!='clean':
            continue
        if not opportunity_profile and listing.get('condition') in ('damaged', 'undamaged') and condition in ('damaged', 'undamaged') and listing['condition'] != condition:
            continue
        cohort.append(p)
        if (abs(p['year']-listing['year'])>policy['year_tolerance']
                or abs(p['mileage_km']-listing['mileage_km'])>policy['mileage_tolerance_km']):
            continue
        nearby.append(p)
        amounts.append(amount)
        sources.append(dict(source_id=source_id, observed_at=p['observed_at'], url=p['url'],
                            amount_eur=amount, price_kind=p.get('price_kind')))
    output.update(sources=sources, observation_count=len(amounts),
                  unresolved_dimensions=['generation', 'trim/engine', 'year/mileage comparability', 'damage', 'total asking amount'])
    if not amounts:
        return dict(output, reason='No matching source amounts; collect sourced analogies')
    from .market_prices import assess
    price_context=assess(listing,cohort,nearby)
    output['market_price_agent']=price_context
    ordered = sorted(amounts)
    p25 = price_context['asking_low_eur']
    minimum = policy['minimum_comparables'] if policy['profile'] in ('exploratory','opportunities') else 5
    if policy['profile'] in ('exploratory','opportunities'):
        priority = (len(amounts) >= minimum and p25-price >= policy['minimum_headroom_eur']
                    and (p25-price)*100 >= price*policy['minimum_discount_percent'])
    else:
        priority = len(amounts) >= minimum and (p25-price)*10 >= p25
    economics=feasibility(listing,price_context)
    if opportunity_profile:
        priority=priority and economics['passes_necessary_budget']
    output['economic_screen']=economics
    return dict(output, status='provisional_family_context', priority_enrichment=priority,
                observed_envelope_eur=dict(low=min(amounts), typical=round(median(amounts)), high=max(amounts)),
                potential_before_repairs_eur=dict(low=min(amounts)-price, high=max(amounts)-price),
                reason='Resolve specifications and price before exact market selection; no repair or net-profit estimate')
