"""Pessimistic price priority, not net profit or a fast-sale guarantee.

Broad collection remains separate. Buffers are business-policy assumptions;
unknown repairs, operating costs and condition never become documented zeroes.
"""
from .margin_policy import minimum_net_margin_eur

VERSION = 'pessimistic-price-priority-v1'
POLICY = dict(version=VERSION, minimum_comparables=3, year_tolerance=1,
              mileage_tolerance_km=20000, minimum_discount_bps=2000,
              sale_stress_bps=1500, minimum_reserve_eur=750,
              basis='lowest_close_comparable_asking_less_15_percent',
              sale_speed_calibrated=False)


def screen(target, peers):
    """Peers already matched by model/condition; tighten year/km for priority."""
    from .price_memory import normalized
    close = [p for p in peers if abs(p['year']-target['year']) <= POLICY['year_tolerance']
             and abs(p['mileage_km']-target['mileage_km']) <= POLICY['mileage_tolerance_km']
             and not p.get('identity_dossier', {}).get('conflicts')]
    result = dict(policy=dict(POLICY), price_priority_passed=False,
                  comparable_count=len(close), lowest_comparable_asking_eur=None,
                  reference_source=None,
                  conservative_exit_scenario_eur=None, asking_discount_eur=None,
                  headroom_after_sale_stress_and_reserve_eur=None,
                  minimum_required_net_margin_eur=minimum_net_margin_eur(target['price_eur']),
                  repair_and_operating_costs_unknown=True, net_margin_eur=None,
                  expected_days_to_sell=None, buy_recommendation=False,
                  final_cost_condition_and_resale_checks_required=True,
                  blocking_reasons=[])
    blockers = result['blocking_reasons']
    if len(close) < POLICY['minimum_comparables']:
        blockers.append('fewer_than_three_close_price_comparables')
    if target.get('identity_dossier', {}).get('conflicts'):
        blockers.append('identity_conflicts_to_resolve')
    # No perfect trim required, but unknown engine/cambio and condition cannot
    # warrant the strongest price label. The broad lead itself is retained.
    for key in ('fuel', 'transmission'):
        if not normalized(target.get(key)) or any(not normalized(p.get(key)) for p in close):
            blockers.append(key+'_comparison_incomplete')
    if close:
        lowest = min(close, key=lambda p: p['price_eur'])
        low = lowest['price_eur']
        exit_price = low*(10000-POLICY['sale_stress_bps'])//10000
        discount = low-target['price_eur']
        remaining = exit_price-target['price_eur']-POLICY['minimum_reserve_eur']
        result.update(lowest_comparable_asking_eur=low,
                      reference_source={k: lowest.get(k) for k in
                                        ('source', 'source_id', 'url', 'observed_at', 'price_eur')},
                      conservative_exit_scenario_eur=exit_price,
                      asking_discount_eur=discount,
                      headroom_after_sale_stress_and_reserve_eur=remaining)
        if discount*10000 < low*POLICY['minimum_discount_bps']:
            blockers.append('purchase_not_at_least_20_percent_below_low_reference')
        if remaining < result['minimum_required_net_margin_eur']:
            blockers.append('insufficient_headroom_after_low_exit_and_reserve')
    from .opportunity_discovery import damage_group
    if damage_group(target) != 'clean':
        blockers.append('repaired_condition_resale_and_costs_require_review')
        # Same-damage asking prices are not a repaired-car resale estimate.
        result['conservative_exit_scenario_eur'] = None
    result['price_priority_passed'] = not blockers
    return result
