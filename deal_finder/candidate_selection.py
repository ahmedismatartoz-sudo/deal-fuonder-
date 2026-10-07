"""Explainable review shortlist, not approval or calibrated probability."""
from collections import Counter
from datetime import datetime, timezone
from math import ceil

VERSION = 'candidate-evidence-balanced-v1'
UNKNOWN = ('', 'unknown', 'unclassified', 'n/a', '-', 'sconosciuto')


def present(value):
    return value is not None and value != [] and value != {} and str(value).strip().lower() not in UNKNOWN


def field_profile(rows):
    fields = ('make', 'model', 'year', 'mileage_km', 'price_eur', 'fuel',
              'transmission', 'version_text', 'city', 'seller_type', 'description',
              'displacement_cc', 'power_hp', 'generation', 'trim', 'condition')
    return dict(sample_count=len(rows), basis='all_discovery_leads_before_shortlist',
                fields={key: dict(usable=sum(present(row.get(key)) for row in rows),
                                  total=len(rows)) for key in fields},
                images_not_measured_in_compact_price_projection=True)


def judge(row, as_of):
    context = row.get('conservative_price_screen') or {}
    low = context.get('lowest_comparable_asking_eur')
    price = row['price_eur']
    required = row['minimum_required_net_margin_eur']
    # Published low-price headroom is not after all costs. A high asking mean
    # cannot by itself generate the price score.
    discount = max(0, (low-price)/low) if low else 0
    headroom = max(0, context.get('headroom_after_sale_stress_and_reserve_eur') or 0)
    price_score = min(20, discount*40)+min(15, headroom/required*10)
    weights = dict(fuel=6, transmission=6, version_text=4, seller_type=3,
                   city=3, description=4, detail_fetched=4)
    evidence = sum(weight for key, weight in weights.items()
                   if present(row.get(key)) and (key != 'detail_fetched' or row[key] is True))
    coverage = min(8, row.get('comparable_count', 0)*8/12)+min(8, context.get('comparable_count', 0)*8/6)
    coverage += 4 if row.get('price_kind') == 'total' else 0
    age = None
    try:
        observed = datetime.fromisoformat(row['observed_at'].replace('Z', '+00:00'))
        if observed.tzinfo is None: raise ValueError('Timezone required')
        age = (as_of-observed).total_seconds()/86400
    except (KeyError, ValueError, TypeError, AttributeError): pass
    freshness = 10 if age is not None and 0 <= age <= 3 else 7 if age is not None and 0 <= age <= 7 else 4 if age is not None and 0 <= age <= 14 else 0
    technical = sum(weight for key, weight in dict(power_hp=2, displacement_cc=2, generation=1).items()
                    if present(row.get(key)))
    flags = []
    penalties = 0
    if (row.get('identity_dossier') or {}).get('conflicts'):
        penalties += 30; flags.append('identity_conflicts_to_resolve')
    if row.get('damage_category') == 'unknown':
        penalties += 10; flags.append('condition_unknown_not_assumed_clean')
    if row.get('price_kind') != 'total':
        penalties += 8; flags.append('total_purchase_price_to_confirm')
    if age is None or age < 0:
        flags.append('observation_time_unusable')
    components = dict(low_price_signal=round(price_score, 2), common_field_evidence=evidence,
                      comparable_coverage=round(coverage, 2), freshness=freshness,
                      technical_evidence=technical, uncertainty_penalty=penalties)
    score = round(max(0, min(100, price_score+evidence+coverage+freshness+technical-penalties)), 2)
    return dict(version=VERSION, score=score, score_is_probability=False,
                status='price_priority' if row.get('price_priority_passed') else 'research_candidate',
                components=components, uncertainty_flags=flags,
                missing_common_fields=[key for key in weights if not present(row.get(key))],
                economic_checks_still_required=True, buy_recommendation=False,
                basis='low_asking_price_evidence_and_review_priority_not_net_profit')


def select(rows, limit, as_of):
    """Four equal price targets, soft family cap; fill shortages transparently."""
    if type(limit) is not int or limit < 1: raise ValueError('Positive integer limit required')
    enriched = [dict(row, candidate_judgment=judge(row, as_of)) for row in rows]
    def order(row):
        return (not row.get('price_priority_passed', False), -row['candidate_judgment']['score'],
                -row.get('comparable_count', 0), row['source'], row['source_id'])
    ranked = sorted(enriched, key=order)
    target = min(limit, len(ranked))
    quotas = [limit//4+(band < limit%4) for band in range(4)]
    family_limit = max(1, ceil(limit*.1))
    families, bands = Counter(), Counter()
    selected, keys = [], set()
    def add(row, cap):
        key = (row['source'], row['source_id'])
        family = (str(row.get('make', '')).lower(), str(row.get('model', '')).lower())
        if key in keys or len(selected) >= target or (cap and families[family] >= family_limit): return False
        keys.add(key); selected.append(row); families[family] += 1; bands[row['price_band']] += 1
        return True
    # Preserve all strongest price-priority cases (up to the report cap).
    for row in ranked:
        if row.get('price_priority_passed'): add(row, False)
    for row in ranked:
        if bands[row['price_band']] < quotas[row['price_band']]: add(row, True)
    # A family cap is a diversification preference, never an exclusion gate.
    for row in ranked:
        if bands[row['price_band']] < quotas[row['price_band']]: add(row, False)
    shortages = [max(0, quotas[i]-bands[i]) for i in range(4)]
    for row in ranked: add(row, True)
    for row in ranked: add(row, False)
    selected.sort(key=order)
    available = Counter(row['price_band'] for row in ranked)
    return selected, dict(version=VERSION, requested_limit=limit, selected_count=len(selected),
                          eligible_count=len(rows), shortlist_not_approved_opportunities=True,
                          target_per_price_band=quotas, available_per_price_band=[available[i] for i in range(4)],
                          selected_per_price_band=[bands[i] for i in range(4)],
                          unfilled_band_targets_before_redistribution=shortages,
                          redistributed_slots=sum(max(0, bands[i]-quotas[i]) for i in range(4)),
                          soft_family_cap=family_limit, family_cap_relaxed=any(v > family_limit for v in families.values()))
