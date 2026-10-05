from datetime import datetime, timezone

MATCH_FIELDS = ('make', 'model', 'generation', 'trim', 'fuel', 'transmission',
                'province', 'seller_type')


def weighted_quantile(items, quantile):
    ordered = sorted(items, key=lambda item: item[0])
    threshold = sum(weight for _, weight in ordered) * quantile
    cumulative = 0.0
    for price, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return price
    return ordered[-1][0]


def estimate(target, listings, *, as_of=None, minimum=8, scope='province', condition='undamaged'):
    """Explainable asking-price benchmark; never a calibrated sale prediction."""
    if condition not in ('undamaged', 'damaged'):
        raise ValueError('Comparable condition must be explicit')
    if scope not in ('province', 'national'):
        raise ValueError('Invalid geographic scope')
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('as_of must be timezone-aware')
    if minimum < 8:
        raise ValueError('minimum cannot be less than 8')
    # Latest snapshot per source; inactive latest observations supersede old active ones.
    latest = {}
    for item in listings:
        stamp = datetime.fromisoformat(item.observed_at)
        if stamp > now:
            continue
        previous = latest.get(item.identity)
        if previous is None or stamp > datetime.fromisoformat(previous.observed_at):
            latest[item.identity] = item
    selected = []
    seen_vehicles = set()
    for item in sorted(latest.values(), key=lambda x: x.observed_at, reverse=True):
        age = (now - datetime.fromisoformat(item.observed_at)).total_seconds() / 86400
        if item.identity == target.identity or (target.vehicle_id and item.vehicle_id == target.vehicle_id):
            continue
        if target.price_kind != 'total' or item.price_kind != 'total':
            continue
        if not item.active or item.condition != condition or not 0 <= age <= 30:
            continue
        if any(getattr(item, field) != getattr(target, field) for field in MATCH_FIELDS if scope != 'national' or field != 'province'):
            continue
        year_gap = abs(item.year - target.year)
        km_gap = abs(item.mileage_km - target.mileage_km)
        if year_gap > 1 or km_gap > 20_000:
            continue
        # Only verified vehicle IDs deduplicate across marketplaces. Unknown IDs
        # are flagged; no speculative merging based on similar price/year/km.
        if item.vehicle_id and item.vehicle_id in seen_vehicles:
            continue
        if item.vehicle_id:
            seen_vehicles.add(item.vehicle_id)
        weight = 1 / (1 + year_gap + km_gap / 20_000 + age / 30)
        selected.append((item, weight))
    comparables = [dict(source=x.source, source_id=x.source_id, url=x.url,
                        price_eur=x.price_eur, province=x.province, weight=round(w, 6), observed_at=x.observed_at)
                   for x, w in selected]
    result = dict(status='insufficient_data', basis='asking_prices',
                  model_version='asking-comparables-v0.3', comparable_condition=condition, geographic_scope=scope, comparable_count=len(selected),
                  comparables=comparables, benchmark_eur=None, observed_range_eur=None,
                  warnings=['Not a sale-price forecast; accuracy has not been calibrated.'])
    if scope == 'national':
        result['warnings'].append('National comparables: local price adjustments have not been calibrated.')
    if target.price_kind != 'total':
        result['warnings'].append('Confirmed total purchase price required.')
    if any(not x.vehicle_id for x, _ in selected):
        result['warnings'].append('Cross-marketplace duplicates may remain without verified vehicle IDs.')
    target_age = (now-datetime.fromisoformat(target.observed_at)).total_seconds()/86400
    if not target.active or not 0 <= target_age <= 30:
        result['warnings'].append('Target must be active and observed within the analysis window.')
        return result
    if len(selected) < minimum:
        result['warnings'].append(f'At least {minimum} comparable listings required.')
        return result
    effective = sum(w for _, w in selected) ** 2 / sum(w*w for _, w in selected)
    if effective < minimum * 0.75:
        result['warnings'].append('Insufficient effective sample size.')
        return result
    pairs = [(x.price_eur, w) for x, w in selected]
    lower, median, upper = [weighted_quantile(pairs, q) for q in (0.25, 0.5, 0.75)]
    if (upper - lower) / median > 0.3:
        result['warnings'].append('Comparable prices too dispersed; manual review required.')
        return result
    result.update(status='benchmark_available', benchmark_eur=median,
                  observed_range_eur={'p25': lower, 'p75': upper},
                  effective_sample_size=round(effective, 2))
    result['warnings'].append('Observed quartiles are not a prediction or confidence interval.')
    return result
