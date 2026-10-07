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
    memory = PriceMemory(db)
    # Development callers may ingest directly; deployed worker builds memory
    # before consuming jobs. Read only new raw observations, never whole families.
    while memory.sync(as_of):
        pass
    amounts, sources = [], []
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
               or abs(p[key]-listing[key]) > tolerance
               for key,tolerance in (('year',policy['year_tolerance']),
                                     ('mileage_km',policy['mileage_tolerance_km']))):
            continue
        fuel, condition = p.get('fuel'), p.get('condition')
        if (isinstance(listing.get('fuel'), str) and isinstance(fuel, str)
                and listing['fuel'].strip() and fuel.strip() and normalize(listing['fuel']) != normalize(fuel)):
            continue
        if listing.get('condition') in ('damaged', 'undamaged') and condition in ('damaged', 'undamaged') and listing['condition'] != condition:
            continue
        amounts.append(amount)
        sources.append(dict(source_id=source_id, observed_at=p['observed_at'], url=p['url'],
                            amount_eur=amount, price_kind=p.get('price_kind')))
    output.update(sources=sources, observation_count=len(amounts),
                  unresolved_dimensions=['generation', 'trim/engine', 'year/mileage comparability', 'damage', 'total asking amount'])
    if not amounts:
        return dict(output, reason='No matching source amounts; collect sourced analogies')
    ordered = sorted(amounts)
    p25 = ordered[(len(ordered)-1)//4]
    minimum = policy['minimum_comparables'] if policy['profile']=='exploratory' else 5
    if policy['profile']=='exploratory':
        priority = (len(amounts) >= minimum and p25-price >= policy['minimum_headroom_eur']
                    and (p25-price)*100 >= price*policy['minimum_discount_percent'])
    else:
        priority = len(amounts) >= minimum and (p25-price)*10 >= p25
    return dict(output, status='provisional_family_context', priority_enrichment=priority,
                observed_envelope_eur=dict(low=min(amounts), typical=round(median(amounts)), high=max(amounts)),
                potential_before_repairs_eur=dict(low=min(amounts)-price, high=max(amounts)-price),
                reason='Resolve specifications and price before exact market selection; no repair or net-profit estimate')
