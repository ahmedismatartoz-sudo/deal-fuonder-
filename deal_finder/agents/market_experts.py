"""Evidence-driven specialists. No invented depreciation or trained parameters."""
from dataclasses import replace
from ..pricing import estimate

VERSION = 'market-experts-v1'
ROLES = {
    'vehicle_specs': 'Match generation, trim/engine, fuel, gearbox and year; request missing identity.',
    'mileage': 'Compare nearby mileage; report separate low, medium and high mileage cohorts.',
    'condition': 'Separate healthy and damaged asking prices; unknown condition is unresolved.',
    'geography': 'Compare province and national evidence without invented location adjustments.',
    'evidence': 'Expose dated URLs, sample size, duplicate risk and source completeness.',
    'candidate_selector': 'Route promising listings to checks and insufficient evidence to enrichment.',
}


def registry():
    return [dict(name=name, version=VERSION, purpose=purpose) for name, purpose in ROLES.items()]


def benchmark(target, candidates, as_of, condition='undamaged', scope='province'):
    value = estimate(target, candidates, as_of=as_of, condition=condition, scope=scope)
    rows = value['comparables']
    # Sparse evidence still receives an explicitly provisional observed envelope.
    # It never enables screening or becomes a probabilistic forecast.
    prices = [x['price_eur'] for x in rows]
    value['provisional_observed_envelope_eur'] = dict(low=min(prices), high=max(prices)) if prices else None
    value['interval_calibrated'] = False
    return value


def review(target, candidates, as_of, decision):
    if target is None:
        return dict(version=VERSION, route='enrichment', specialists={},
                    next_tasks=['normalize_listing', 'resolve_vehicle_specs'],
                    candidate=False, publishable=False)
    healthy = benchmark(target, candidates, as_of)
    national = benchmark(target, candidates, as_of, scope='national')
    damaged = benchmark(target, candidates, as_of, condition='damaged')
    # Bands describe population segments, never a fixed km depreciation formula.
    mileage = {}
    for name, low, high in (('low', 0, 60000), ('medium', 60000, 150000), ('high', 150000, 2000001)):
        cohort = [x for x in candidates if low <= x.mileage_km < high]
        # Each band uses its median mileage as anchor; not the target's km value.
        kms = sorted(x.mileage_km for x in cohort if (x.make, x.model, x.generation, x.trim, x.fuel, x.transmission)
                     == (target.make, target.model, target.generation, target.trim, target.fuel, target.transmission))
        anchor = kms[len(kms)//2] if kms else target.mileage_km
        mileage[name] = dict(bounds_km=[low, high-1], anchor_km=anchor,
                            benchmark=benchmark(replace(target, mileage_km=anchor), cohort, as_of))
    route = decision.get('route', 'verification' if decision['candidate'] else 'screened_out')
    return dict(version=VERSION, route=route, candidate=decision['candidate'], publishable=False,
        specialists={
            'vehicle_specs': dict(cohort_fields=['make', 'model', 'generation', 'trim', 'fuel', 'transmission'],
                                  exact_plate_identity_required_for_parts=True),
            'mileage': dict(target_km=target.mileage_km, bands=mileage, fixed_depreciation_applied=False),
            'condition': dict(reported=target.condition, as_is=damaged if target.condition == 'damaged' else
                              healthy if target.condition == 'undamaged' else None,
                              healthy_reference=healthy, damaged_reference=damaged,
                              repaired_resale_forecast_eur=None,
                              missing_details=['inspection', 'damage_type', 'post_repair_quality', 'accident_history']),
            'geography': dict(province=healthy, national=national, calibrated_adjustment_eur=None),
            'evidence': dict(basis='asking_prices_not_transactions', calibrated=False,
                             source_urls=[x['url'] for x in healthy['comparables']],
                             warnings=healthy['warnings'], data_kind='source_claims'),
            'candidate_selector': decision},
        next_tasks=['verify_identity', 'inspect_damage', 'research_parts_web', 'estimate_hours',
                    'document_other_costs', 'supervisor'] if decision['candidate'] else
                   ['resolve_identity', 'collect_more_comparables', 'resolve_condition'] if route == 'enrichment' else [])
