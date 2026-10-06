"""Coarse, provisional family prices for enrichment priority, never valuation."""
import re
from datetime import timedelta
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
    make_field, model_field = db.json_field('make'), db.json_field('model')
    rows = db.execute('''SELECT source_id, observed_at, url, price_eur, price_kind, payload
        FROM listing_events e WHERE source=? AND active=? AND observed_at BETWEEN ? AND ?
        AND lower('''+make_field+''')=? AND lower('''+model_field+''')=?
        AND NOT EXISTS (SELECT 1 FROM listing_events n WHERE n.source=e.source AND n.source_id=e.source_id
            AND n.observed_at>e.observed_at AND n.observed_at<=?)''',
        (listing['source'], True, (as_of-timedelta(days=30)).isoformat(), as_of.isoformat(), make, model, as_of.isoformat()))
    amounts, sources = [], []
    for source_id, observed, url, amount, kind, payload in rows:
        if source_id == listing['source_id'] or kind in ('installment', 'deposit') or type(amount) is not int or amount <= 0:
            continue
        payload = db.json_decode(payload)
        text = str(payload.get('title', ''))+' '+str(payload.get('description', ''))
        if kind != 'total' and re.search(r'\b(?:anticipo|acconto|rata|rate mensili)\b|(?:€|eur)\s*/\s*mese', text, re.I):
            continue
        # Known contradictions are not silently pooled into the family analogy.
        if (isinstance(listing.get('fuel'), str) and isinstance(payload.get('fuel'), str)
                and listing['fuel'].strip() and payload['fuel'].strip()
                and normalize(listing['fuel']) != normalize(payload['fuel'])):
            continue
        if listing.get('condition') in ('damaged', 'undamaged') and payload.get('condition') in ('damaged', 'undamaged') and listing['condition'] != payload['condition']:
            continue
        amounts.append(amount)
        sources.append(dict(source_id=source_id, observed_at=observed, url=url, amount_eur=amount, price_kind=kind))
    output.update(sources=sources, observation_count=len(amounts),
                  unresolved_dimensions=['generation', 'trim/engine', 'year/mileage comparability', 'damage', 'total asking amount'])
    if not amounts:
        return dict(output, reason='No matching source amounts; collect sourced analogies')
    ordered = sorted(amounts)
    p25 = ordered[(len(ordered)-1)//4]
    priority = len(amounts) >= 5 and (p25-price)*10 >= p25
    return dict(output, status='provisional_family_context', priority_enrichment=priority,
                observed_envelope_eur=dict(low=min(amounts), typical=round(median(amounts)), high=max(amounts)),
                potential_before_repairs_eur=dict(low=min(amounts)-price, high=max(amounts)-price),
                reason='Resolve specifications and price before exact market selection; no repair or net-profit estimate')
