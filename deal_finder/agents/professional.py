"""Evidence-driven specialists and conservative, non-calibrated admission policy.

Policy buffers are explicit business assumptions, not measured probabilities.
No seller claim, asking-price scenario or supplied attestation enables forecasts.
"""
from datetime import timedelta
from math import ceil, isfinite
import re
from .contracts import bounded_cost, cents, evidence, instant
from ..models import normalize
from ..margin_policy import minimum_net_margin_eur, policy as margin_policy

VERSION = 'professional-opportunity-v2'
POLICY = dict(version=VERSION, minimum_margin_cents=200000,
              net_margin_schedule=margin_policy(),
              minimum_return_bps=2500, severe_minimum_return_bps=3000,
              minimum_comparables=12, severe_minimum_comparables=20,
              severe_repaired_comparables=8, comparable_max_age_days=14,
              maximum_purchase_cents=2000000,
              sale_stress_bps=1000, severe_sale_stress_bps=1500,
              repair_stress_bps=2500, severe_repair_stress_bps=4000,
              minimum_reserve_cents=75000, severe_minimum_reserve_cents=200000,
              reserve_repair_bps=1500, severe_reserve_repair_bps=3000,
              additional_sale_shock_bps=500, additional_repair_shock_bps=1500,
              severe_additional_sale_shock_bps=500, severe_additional_repair_shock_bps=2000,
              minimum_holding_days=45, severe_minimum_holding_days=90,
              forecast_calibrated=False)
SPEC_FIELDS = ('make', 'model', 'generation', 'trim', 'fuel', 'transmission', 'year')
BODYWORK_LINES = ('strip_and_measure', 'structural_work', 'paint', 'materials',
                  'restraints', 'diagnostics', 'adas_calibration', 'alignment',
                  'subcontract_transport')
COLLISION_CHECKS = ('structural_measurement', 'oem_repairability', 'restraints_scope',
                    'hidden_damage_inspection', 'diagnostic_and_adas_plan',
                    'repair_history_resale', 'bodyshop_capacity_and_schedule')
TASKS = {
    'collection': ('track_price_changes_removals_and_reposts', 'review_duplicate_identities'),
    'identity': ('resolve_generation_engine_gearbox_trim', 'record_field_evidence_and_conflicts'),
    'market': ('justify_comparable_inclusions_and_exclusions', 'separate_sellers_conditions_and_geography'),
    'damage': ('map_observed_damage_to_photos', 'separate_required_and_cosmetic_work', 'inspect_hidden_damage'),
    'parts': ('confirm_exact_codes_and_fitment', 'compare_oe_aftermarket_used_separately', 'verify_all_in_purchase_cost'),
    'labor': ('document_operation_hours_and_consumables', 'resolve_shared_disassembly_without_double_counting'),
    'resale': ('compare_disclosed_repaired_history', 'document_fast_sale_and_patient_sale_evidence'),
    'opportunity': ('compute_all_costs_and_stress_margin', 'account_for_capital_storage_and_insurance',
                    'test_lower_resale_and_higher_repairs_together', 'compute_maximum_offer'),
    'supervisor': ('resolve_blocking_evidence', 'challenge_weakest_assumptions'),
    'publication': ('recheck_source_and_analysis_freshness', 'enforce_margin_and_review_again'),
    'evaluation': ('freeze_predictions_before_outcomes', 'measure_overpricing_and_cost_underestimation'),
}


def registry():
    return [dict(name=name, tasks=list(tasks), output_contract=[
        'conclusion', 'evidence', 'uncertainties', 'blocking_reasons', 'next_tasks'],
        source_claims_are_untrusted=True) for name, tasks in TASKS.items()]


def reference_matches(row, target):
    """Every professional document binds to the exact source observation."""
    return (isinstance(row, dict) and target is not None
            and row.get('source') == target.source and row.get('source_id') == target.source_id
            and row.get('observed_at') == target.observed_at
            and (not target.vehicle_id or row.get('vehicle_id') == target.vehicle_id))


def document(row, target, as_of, *, maximum_days=30):
    try:
        if not reference_matches(row, target) or row.get('verified') is not True:
            raise ValueError('Document must be reviewed and bound to the exact vehicle observation')
        if not isinstance(row.get('verified_by'), str) or not row['verified_by'].strip():
            raise ValueError('Document reviewer required')
        evidence(row['evidence_url'])
        if not timedelta(0) <= as_of - instant(row['checked_at']) <= timedelta(days=maximum_days):
            raise ValueError('Document is stale or from the future')
        return None
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
        return str(error)


def dossier(raw):
    value = raw.get('professional_evidence', {})
    return value if isinstance(value, dict) else {}


def result(agent, blockers, **data):
    return dict(agent=agent, version=VERSION, status='needs_evidence' if blockers else 'completed',
                evidence=data.pop('evidence', []), blocking_reasons=list(dict.fromkeys(blockers)),
                data=data, buy_recommendation=False)


class AnnouncementAnomalyAgent:
    """Text is a review signal, never proof of fraud or seller intent."""
    name = 'announcement_anomalies'

    def execute(self, raw, target, candidates, as_of):
        if target is None:
            return result(self.name, ['Valid listing required'], hard_blockers=['invalid_listing'])
        text = ' '.join((target.title, target.description)).casefold()
        patterns = {
            'conditional_financing_price': r'(?:prezzo.{0,25}(?:solo|con|vincolato).{0,20}finanziamento|finanziamento obbligatorio)',
            'deposit_or_installment': r'(?:prezzo.{0,15}(?:acconto|anticipo)|solo\s+\d+\s*(?:€|euro)\s*(?:al mese|/mese)|rata mensile)',
            'parts_only_vehicle': r'(?:solo (?:per )?ricambi|uso ricambi|non vend(?:o|esi) intera)',
            'unresolved_mileage': r'(?:km non (?:certificati|verificabili)|chilometri (?:scalati|non verificabili))',
            'documents_unavailable': r'(?:senza documenti|documenti mancanti|fermo amministrativo)',
        }
        flags = [key for key, pattern in patterns.items() if re.search(pattern, text)]
        if target.price_kind != 'total':
            flags.append('non_total_price')
        if not target.active:
            flags.append('inactive_source')
        conflicts, duplicates = [], []
        for item in candidates:
            if target.vehicle_id and item.vehicle_id == target.vehicle_id:
                if any(getattr(item, key) != getattr(target, key) for key in SPEC_FIELDS):
                    conflicts.append(item.url)
                if item.identity != target.identity:
                    duplicates.append(item.url)
        if conflicts:
            flags.append('conflicting_vehicle_identity')
        confirmations = dossier(raw).get('acquisition')
        blockers = []
        error = document(confirmations, target, as_of, maximum_days=1)
        if error:
            blockers.append('Verify full purchase price, availability and acquisition conditions: ' + error)
        else:
            for key in ('total_price_confirmed', 'available', 'documents_checked', 'mileage_checked'):
                if confirmations.get(key) is not True:
                    blockers.append('Acquisition check unresolved: ' + key)
            if confirmations.get('purchase_price_cents') != target.price_eur * 100:
                blockers.append('Confirmed purchase price differs from listing; create a new observation')
            resolved = confirmations.get('resolved_flags', [])
            flags = [flag for flag in flags if flag not in resolved
                     or flag in ('inactive_source', 'non_total_price', 'conflicting_vehicle_identity')]
        blockers.extend('Announcement signal requires review: ' + flag for flag in flags)
        return result(self.name, blockers, signals=flags, hard_blockers=flags,
                      conflicting_urls=conflicts, duplicate_urls=sorted(set(duplicates)),
                      evidence=[target.url], fraud_diagnosis=False,
                      next_tasks=['verify_total_price_and_conditions', 'verify_vehicle_documents'])


def damage_severity(raw, target, as_of):
    text = (' '.join((target.title, target.description)) if target else '').casefold()
    # Negation is handled conservatively per phrase; no text classifier certifies safety.
    phrases = (r'gravemente incidentat\w*', r'severely damaged', r'structural damage',
               r'longheron\w* (?:piegat\w*|deformat\w*|danneggiat\w*)',
               r'telaio (?:piegato|deformato|danneggiato)', r'airbag\w* (?:scoppiat\w*|esplos\w*|deployed)',
               r'alluvionat\w*', r'incendiat\w*', r'auto bruciat\w*')
    signals = []
    for pattern in phrases:
        for match in re.finditer(pattern, text):
            prefix = text[max(0, match.start()-24):match.start()]
            if not re.search(r'\b(?:non|no|nessun[oa]?|senza|mai)\s+(?:è\s+|stata\s+|stato\s+)?$', prefix):
                signals.append(match.group())
    assessment = dossier(raw).get('damage_assessment')
    declared = assessment.get('severity') if isinstance(assessment, dict) else None
    if target and target.damage_severity == 'severe':
        declared = 'severe'
    if target and target.damage_indicators:
        signals.extend(target.damage_indicators)
    inspection = raw.get('inspection')
    if isinstance(inspection, dict) and inspection.get('severity') == 'severe':
        declared = 'severe'
    error = document(assessment, target, as_of)
    if signals or declared == 'severe':
        return dict(severity='severe', severe_controls_required=True, signals=signals,
                    basis='review_signal_or_severe_assessment', assessment_error=error)
    if error is None and declared in ('none', 'minor', 'moderate'):
        if target.condition == 'damaged' and declared == 'none':
            return dict(severity='unknown', severe_controls_required=True, signals=[],
                        basis='condition_assessment_conflict', assessment_error='Damaged listing conflicts with none')
        return dict(severity=declared, severe_controls_required=False, signals=[],
                    basis='reviewed_assessment', assessment_error=None)
    return dict(severity='unknown', severe_controls_required=bool(target and target.condition == 'damaged'),
                signals=[], basis='unresolved_damage', assessment_error=error or 'Damage severity missing')


class TechnicalRiskAgent:
    name = 'technical_risk'

    def execute(self, raw, target, as_of):
        severity = damage_severity(raw, target, as_of)
        blockers = []
        if severity['assessment_error']:
            blockers.append('Reviewed damage severity required: ' + severity['assessment_error'])
        checks = dossier(raw).get('collision_checks', {})
        checks = checks if isinstance(checks, dict) else {}
        completed = []
        if severity['severe_controls_required']:
            for key in COLLISION_CHECKS:
                check = checks.get(key)
                error = document(check, target, as_of)
                if error or check.get('passed') is not True or not check.get('findings'):
                    blockers.append('Severe collision check unresolved: ' + key)
                else:
                    completed.append(dict(check=key, evidence_url=check['evidence_url'], findings=check['findings']))
        reports = dossier(raw).get('model_risks', [])
        findings, rejected = [], []
        if not isinstance(reports, list) or len(reports) > 50:
            blockers.append('Model risk reports must be an array of at most 50 items')
            reports = []
        for report in reports:
            error = document(report, target, as_of)
            specs = report.get('specifications', {}) if isinstance(report, dict) else {}
            if not error and (not isinstance(specs, dict) or any(
                    specs.get(key) != getattr(target, key) for key in SPEC_FIELDS)):
                error = 'Model risk report does not match the exact variant'
            if not error and (not report.get('finding') or not report.get('required_check')):
                error = 'Technical finding and vehicle-specific check required'
            if error:
                rejected.append(dict(reason=error))
                blockers.append('Model risk report requires correction')
            else:
                findings.append(dict(finding=report['finding'], required_check=report['required_check'],
                                     evidence_url=report['evidence_url'], diagnosed_on_vehicle=False))
                if report.get('vehicle_check_passed') is not True:
                    blockers.append('Model-specific inspection unresolved: ' + report['required_check'])
        completion = dossier(raw).get('model_risk_review')
        error = document(completion, target, as_of)
        if error or completion.get('coverage_complete') is not True:
            blockers.append('Review variant-specific documented problems; no report is not proof of no risk')
        else:
            try:
                sources = completion.get('sources_consulted')
                if not isinstance(sources, list) or len(set(sources)) < 2:
                    raise ValueError('Variant risk review requires at least two distinct consulted sources')
                for url in sources:
                    evidence(url)
                if completion.get('specifications') != {key:getattr(target,key) for key in SPEC_FIELDS}:
                    raise ValueError('Variant risk review specifications do not match the vehicle')
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                blockers.append(str(error))
        return result(self.name, blockers, **severity, completed_collision_checks=completed,
                      model_findings=findings, rejected_reports=rejected,
                      evidence=[x['evidence_url'] for x in completed+findings],
                      next_tasks=['consult_oem_repair_procedures', 'verify_variant_specific_problems'],
                      probabilities_calibrated=False, hidden_damage_certified_by_photos=False)


def bodywork_cost(raw, target, risk, as_of):
    if not risk['data']['severe_controls_required']:
        return dict(required=False, high_cents=0, blocking_reasons=[], covered_repair_ids=[])
    value = dossier(raw).get('bodywork_quote')
    blockers = []
    error = document(value, target, as_of)
    if error:
        return dict(required=True, high_cents=None, covered_repair_ids=[],
                    blocking_reasons=['Specialist bodyshop quote required: ' + error])
    try:
        if value.get('provider_type') != 'external_bodyshop' or not value.get('provider_name'):
            raise ValueError('Use an identified external bodyshop; no generic in-house repair discount')
        low, high = bounded_cost(value, as_of)
        if high <= 0:
            raise ValueError('Severe bodywork cannot be priced as a free external repair')
        lines = value['lines']
        if not isinstance(lines, dict) or set(lines) != set(BODYWORK_LINES):
            raise ValueError('Bodyshop quote must explicitly cover every collision work category')
        costs = {key: bounded_cost(row, as_of) for key, row in lines.items()}
        if low != sum(row[0] for row in costs.values()) or high != sum(row[1] for row in costs.values()):
            raise ValueError('Bodyshop quote totals do not match detailed work lines')
        repair_ids = value.get('covered_repair_ids')
        if not isinstance(repair_ids, list) or not repair_ids or len(set(repair_ids)) != len(repair_ids):
            raise ValueError('Bodywork quote needs distinct covered repair IDs')
        required = (raw.get('inspection') or {}).get('required_repairs', [])
        if not set(repair_ids).issubset(required):
            raise ValueError('Bodywork scope differs from inspection')
        if value.get('scope_complete') is not True or value.get('fixed_scope') is not True:
            raise ValueError('Open-ended bodyshop scope cannot pass final checks')
        return dict(required=True, high_cents=high, low_cents=low, covered_repair_ids=repair_ids,
                    evidence_url=value['evidence_url'], lines=costs, provider_name=value['provider_name'],
                    blocking_reasons=[])
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
        return dict(required=True, high_cents=None, covered_repair_ids=[], blocking_reasons=[str(error)])


def comparable_audit(raw, target, market, as_of, risk):
    rows = market.get('comparables', [])
    if not isinstance(rows, list):
        rows = []
    required = POLICY['severe_minimum_comparables'] if risk['data']['severe_controls_required'] else POLICY['minimum_comparables']
    documents = dossier(raw).get('comparable_identities', [])
    identities = {}
    blockers, accepted, excluded, seen = [], [], [], set()
    if isinstance(documents, list):
        for proof in documents:
            if document(proof, target, as_of) is None:
                key = (proof.get('comparable_source'), proof.get('comparable_source_id'), proof.get('comparable_observed_at'))
                if isinstance(proof.get('comparable_vehicle_id'), str) and proof['comparable_vehicle_id'].strip():
                    if key in identities and identities[key]['comparable_vehicle_id'] != proof['comparable_vehicle_id']:
                        blockers.append('Conflicting comparable identities')
                    identities[key] = proof
    for row in rows:
        try:
            key = (row['source'], row['source_id'], row['observed_at'])
            if not timedelta(0) <= as_of-instant(row['observed_at']) <= timedelta(days=POLICY['comparable_max_age_days']):
                raise ValueError('Comparable older than strict window')
            evidence(row['url'])
            proof = identities.get(key, {})
            vehicle = proof.get('comparable_vehicle_id')
            if not vehicle or vehicle == target.vehicle_id or vehicle in seen:
                raise ValueError('Physical identity unverified, target itself or duplicate comparable')
            if proof.get('specifications_verified') is not True or proof.get('specifications') != row.get('specifications'):
                raise ValueError('Comparable specifications are not independently reviewed')
            if type(row.get('price_eur')) is not int or row['price_eur'] <= 0:
                raise ValueError('Comparable price invalid')
            if proof.get('price_cents') != row['price_eur']*100:
                raise ValueError('Reviewed comparable price disagrees with observation')
            weight = row.get('weight')
            if type(weight) not in (float, int) or not isfinite(weight) or not 0 < weight <= 1:
                raise ValueError('Comparable evidence weight invalid')
            seen.add(vehicle)
            accepted.append(dict(row, verified_vehicle_id=vehicle))
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
            excluded.append(dict(url=row.get('url') if isinstance(row, dict) else None, reason=str(error)))
    if len(accepted) < required:
        blockers.append(f'At least {required} recent, unique, reviewed comparable vehicles required')
    if market.get('geographic_scope') != 'province':
        blockers.append('Local comparable coverage required; national fallback remains research only')
    from ..pricing import weighted_quantile
    pairs = [(row['price_eur'], row['weight']) for row in accepted]
    interval = None
    if pairs:
        low, median, high = [weighted_quantile(pairs, q) for q in (0.25, 0.5, 0.75)]
        effective = sum(w for _, w in pairs)**2 / sum(w*w for _, w in pairs)
        if effective < required*0.75 or (high-low)/median > 0.3:
            blockers.append('Reviewed comparable evidence has insufficient effective size or excessive dispersion')
        interval = dict(p25=low, p75=high)
    return dict(required_count=required, accepted_count=len(accepted), included=accepted,
                excluded=excluded, blocking_reasons=blockers, reviewed_range_eur=interval)


def accident_resale(raw, target, risk, as_of):
    if not risk['data']['severe_controls_required']:
        return dict(required=False, conservative_ceiling_cents=None, blocking_reasons=[])
    rows = dossier(raw).get('repaired_history_comparables', [])
    accepted, rejected, seen = [], [], set()
    if not isinstance(rows, list) or len(rows) > 100:
        rows = []
    for row in rows:
        try:
            if document(row, target, as_of, maximum_days=14):
                raise ValueError('Reviewed recent repaired-history observation required')
            specs = row['specifications']
            if any(specs.get(k) != getattr(target, k) for k in SPEC_FIELDS if k != 'year'):
                raise ValueError('Repaired comparable variant mismatch')
            if type(specs.get('year')) is not int or abs(specs['year']-target.year) > 1:
                raise ValueError('Repaired comparable year mismatch')
            km = row['mileage_km']
            if type(km) is not int or km < 0 or abs(km-target.mileage_km) > 20000:
                raise ValueError('Repaired comparable mileage mismatch')
            if row.get('seller_type') != target.seller_type or normalize(row.get('province')) != target.province:
                raise ValueError('Repaired comparable seller/geography mismatch')
            identity = row['comparable_vehicle_id']
            if not isinstance(identity, str) or not identity.strip() or identity == target.vehicle_id or identity in seen:
                raise ValueError('Repaired comparable physical identity missing or duplicated')
            if row.get('prior_damage_severity') != 'severe' or row.get('repair_history_disclosed') is not True:
                raise ValueError('Disclosed severe repair history required; healthy cars are not equivalent')
            if row.get('active') is not True or row.get('price_kind') != 'total':
                raise ValueError('Active total-price repaired comparable required')
            price = cents(row['price_cents'])
            if price == 0:
                raise ValueError('Positive repaired comparable price required')
            seen.add(identity)
            accepted.append(dict(vehicle_id=identity, price_cents=price, evidence_url=row['evidence_url']))
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
            rejected.append(dict(reason=str(error)))
    blockers = [] if len(accepted) >= POLICY['severe_repaired_comparables'] else [
        f"At least {POLICY['severe_repaired_comparables']} unique comparable cars with disclosed severe repair history required"]
    prices = sorted(row['price_cents'] for row in accepted)
    # This is an observed conservative ceiling, never a transaction forecast.
    ceiling = prices[max(0, ceil(len(prices)*0.25)-1)] if prices else None
    return dict(required=True, conservative_ceiling_cents=ceiling, included=accepted,
                excluded=rejected, blocking_reasons=blockers, basis='repaired_history_asking_prices')


def holding_cost(raw, target, severe, as_of):
    """Carrying costs are evidenced daily amounts, not invented sale-time forecasts."""
    plan = dossier(raw).get('holding_plan')
    minimum_days = POLICY['severe_minimum_holding_days' if severe else 'minimum_holding_days']
    value = dict(high_cents=None, stress_days=None, minimum_days=minimum_days,
                 basis='planning_horizon_not_predicted_sale_time', blocking_reasons=[])
    error = document(plan, target, as_of)
    if error:
        value['blocking_reasons'] = ['Document capital, storage and insurance holding costs: '+error]
        return value
    try:
        days = plan.get('planned_days')
        if type(days) is not int or not minimum_days <= days <= 730:
            raise ValueError(f'At least {minimum_days} stress holding days required')
        costs = plan['daily_costs']
        if not isinstance(costs,dict) or set(costs) != {'funding','storage','insurance'}:
            raise ValueError('All three holding-cost categories must be documented')
        rates = {}
        for category,row in costs.items():
            if not isinstance(row,dict) or row.get('unit') != 'day':
                raise ValueError('Holding costs require a documented daily unit')
            rates[category] = bounded_cost(row,as_of)[1]
            if rates[category] == 0 and (not isinstance(row.get('zero_basis'),str) or not row['zero_basis'].strip()):
                raise ValueError('Zero holding costs require an explicit documented reason')
        value.update(high_cents=sum(rates.values())*days, stress_days=days,
                     daily_high_cents=rates, additional_to_operating_costs=True)
    except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as error:
        value['blocking_reasons'] = [str(error)]
    return value


def conservative_economics(raw, target, market, repair, opportunity, risk, as_of):
    blockers = []
    severe = risk['data']['severe_controls_required']
    bodyshop = bodywork_cost(raw, target, risk, as_of)
    blockers.extend(bodyshop['blocking_reasons'])
    repairs_high, operations_high = None, None
    try:
        if bodyshop['required'] and bodyshop['blocking_reasons']:
            raise ValueError('Unresolved specialist bodyshop scope blocks repair cost and margin')
        if repair.get('status') != 'completed' or opportunity.get('status') != 'completed':
            raise ValueError('Complete reviewed repairs and all seven operating costs required')
        repairs_high = cents(repair['data']['high_cents'])
        # Independently recalculate from evidence, not from an upstream total.
        categories = ('transfer', 'transport', 'preparation', 'warranty', 'taxes', 'fees', 'contingency')
        costs = raw['operating_costs']
        if not isinstance(costs, dict) or set(costs) != set(categories):
            raise ValueError('Every operating cost category is mandatory')
        operations_high = sum(bounded_cost(costs[key], as_of)[1] for key in categories)
        required_repairs = repair['data'].get('quotes', [])
        recomputed = sum(bounded_cost(row, as_of)[1] for row in required_repairs)
        if recomputed != repairs_high:
            raise ValueError('Repair total disagrees with underlying evidence')
        if bodyshop['required'] and bodyshop['high_cents'] is not None:
            covered = [q for q in required_repairs if q['repair_id'] in bodyshop['covered_repair_ids']]
            covered_total = sum(bounded_cost(q, as_of)[1] for q in covered)
            if set(q['repair_id'] for q in covered) != set(bodyshop['covered_repair_ids']):
                raise ValueError('Bodyshop quote must be included in complete repair quotes')
            if covered_total < bodyshop['high_cents']:
                blockers.append('Repair quotes understate specialist bodyshop cost')
                repairs_high += bodyshop['high_cents']-covered_total
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
        blockers.append(str(error))
        repairs_high, operations_high = None, None
    resale = accident_resale(raw, target, risk, as_of)
    blockers.extend(resale['blocking_reasons'])
    holding = holding_cost(raw,target,severe,as_of)
    blockers.extend(holding['blocking_reasons'])
    required_margin=minimum_net_margin_eur(target.price_eur)*100 if target is not None else POLICY['minimum_margin_cents']
    value = dict(policy=dict(POLICY), basis='asking_price_stress_scenario_only',
                 margin_low_cents=None, maximum_offer_cents=None, total_investment_cents=None,
                 minimum_margin_cents=required_margin, passes_margin=False,
                 passes_return=False, passes_resilience=False, sensitivity_scenarios=[],
                 bodyshop=bodyshop, repaired_history=resale, holding=holding,
                 repair_cost_high_cents=repairs_high, operating_cost_high_cents=operations_high,
                 sale_stress_cents=None, repair_stress_cents=None, reserve_cents=None,
                 forecast_profit_cents=None, buy_recommendation=False)
    interval = market.get('observed_range_eur')
    try:
        if target is None or not isinstance(interval, dict):
            raise ValueError('Comparable price reference unavailable')
        reference = cents(interval['p25']*100)
        rows=market.get('reviewed_comparables',market.get('comparables',[]))
        valid_prices=[r['price_eur']*100 for r in rows if isinstance(r,dict) and type(r.get('price_eur')) is int and r['price_eur']>0]
        if valid_prices:
            reference=min(reference,min(valid_prices))
        if type(interval['p25']) is not int or reference == 0:
            raise ValueError('Positive whole-EUR price reference required')
        if repairs_high is None or operations_high is None:
            raise ValueError('Unknown costs block margin and maximum offer calculations')
        if holding['high_cents'] is None:
            raise ValueError('Unknown holding costs block margin and maximum offer calculations')
        if severe:
            if resale['blocking_reasons'] or resale['conservative_ceiling_cents'] is None:
                raise ValueError('Severe repaired-history resale reference unavailable')
            reference = min(reference, resale['conservative_ceiling_cents'])
        sale_stress = (reference * POLICY['severe_sale_stress_bps' if severe else 'sale_stress_bps'] + 9999)//10000
        repair_stress = (repairs_high * POLICY['severe_repair_stress_bps' if severe else 'repair_stress_bps'] + 9999)//10000
        reserve = max(POLICY['severe_minimum_reserve_cents' if severe else 'minimum_reserve_cents'],
                      (repairs_high*POLICY['severe_reserve_repair_bps' if severe else 'reserve_repair_bps']+9999)//10000)
        exit_low = reference-sale_stress
        base_costs = repairs_high+operations_high+repair_stress+reserve+holding['high_cents']
        rate = POLICY['severe_minimum_return_bps' if severe else 'minimum_return_bps']
        sale_shock = (reference*POLICY['severe_additional_sale_shock_bps' if severe else 'additional_sale_shock_bps']+9999)//10000
        repair_shock = (repairs_high*POLICY['severe_additional_repair_shock_bps' if severe else 'additional_repair_shock_bps']+9999)//10000
        scenarios = []
        for name,sale_delta,repair_delta in (('base',0,0),('lower_resale',sale_shock,0),
                                            ('higher_repairs',0,repair_shock),('combined_adverse',sale_shock,repair_shock)):
            investment = target.price_eur*100+base_costs+repair_delta
            margin = exit_low-sale_delta-investment
            scenarios.append(dict(name=name,exit_cents=exit_low-sale_delta,investment_cents=investment,
                                  margin_cents=margin,passes_margin=margin>=required_margin,
                                  passes_return=margin*10000>=investment*rate))
        worst = scenarios[-1]
        costs = base_costs+repair_shock
        margin,investment = worst['margin_cents'],worst['investment_cents']
        exit_low = worst['exit_cents']
        maximum = min(exit_low-required_margin-costs,
                      exit_low*10000//(10000+rate)-costs, POLICY['maximum_purchase_cents'])
        value.update(reference_cents=reference, conservative_exit_cents=exit_low,
                     sale_stress_cents=sale_stress, repair_stress_cents=repair_stress,
                     reserve_cents=reserve, total_cost_high_cents=costs,
                     base_exit_cents=scenarios[0]['exit_cents'],base_cost_high_cents=base_costs,
                     additional_sale_shock_cents=sale_shock,additional_repair_shock_cents=repair_shock,
                     sensitivity_scenarios=scenarios,
                     passes_resilience=all(row['passes_margin'] and row['passes_return'] for row in scenarios),
                     margin_low_cents=margin, total_investment_cents=investment,
                     maximum_offer_cents=max(0, maximum), minimum_return_bps=rate,
                     passes_margin=margin >= required_margin,
                     passes_return=margin*10000 >= investment*rate,
                     budget_passed=target.price_eur*100 <= POLICY['maximum_purchase_cents'],
                     scenario_calibrated=False)
        if not value['passes_margin']:
            blockers.append('Conservative margin below '+str(required_margin//100)+' EUR')
        if not value['passes_return']:
            blockers.append('Conservative return on all invested capital below policy')
        if not value['passes_resilience']:
            blockers.append('Margin or return fails simultaneous adverse resale/repair stress')
        if not value['budget_passed']:
            blockers.append('Purchase must be at most 20000 EUR')
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
        blockers.append(str(error))
    value['blocking_reasons'] = list(dict.fromkeys(blockers))
    value['costs_complete'] = (repairs_high is not None and operations_high is not None
                              and not bodyshop['blocking_reasons'] and not holding['blocking_reasons'])
    return value


class IndependentReviewAgent:
    """An independent deterministic recalculation, not a second trained AI model."""
    name = 'independent_review'

    def execute(self, raw, target, market, repair, opportunity, candidates, as_of):
        anomaly = AnnouncementAnomalyAgent().execute(raw, target, candidates, as_of)
        risk = TechnicalRiskAgent().execute(raw, target, as_of)
        from .repair_planning import execute as check_repairs
        planning = check_repairs(raw, target, as_of)
        audit = comparable_audit(raw, target, market, as_of, risk)
        if audit['reviewed_range_eur']:
            market = dict(market, observed_range_eur=audit['reviewed_range_eur'],reviewed_comparables=audit['included'])
        economics = conservative_economics(raw, target, market, repair, opportunity, risk, as_of)
        blockers = anomaly['blocking_reasons']+risk['blocking_reasons']+audit['blocking_reasons']+economics['blocking_reasons']+planning['blocking_reasons']
        # Reviewed and independently generated analysis remain distinct attestations.
        attestation = dossier(raw).get('independent_review')
        error = document(attestation, target, as_of, maximum_days=1)
        primary = {x.get('verified_by') for x in [raw.get('identity_evidence'), raw.get('inspection'),
                   dossier(raw).get('damage_assessment')] if isinstance(x, dict)}
        if error or attestation.get('passed') is not True or attestation.get('verified_by') in primary:
            blockers.append('A distinct reviewer must verify finalist evidence and assumptions')
        elif (attestation.get('margin_low_cents') != economics['margin_low_cents']
              or attestation.get('total_investment_cents') != economics['total_investment_cents']
              or economics['margin_low_cents'] is None or not attestation.get('rationale')):
            blockers.append('Independent reviewer must confirm the recomputed stressed economics and rationale')
        if market.get('status') != 'benchmark_available':
            blockers.append('Available market benchmark required')
        return dict(agent=self.name, version=VERSION, status='needs_evidence' if blockers else 'completed',
                    approved_for_final_checks=not blockers, final_opportunity_approved=False,
                    reviewer_kind='independent_deterministic_recalculation_plus_distinct_human_review',
                    blocking_reasons=list(dict.fromkeys(blockers)), anomaly=anomaly, technical_risk=risk,
                    comparable_audit=audit, economics=economics, repair_planning=planning, forecast_calibrated=False)


def coordinate(review, *, selected, calibrated=False):
    """A dependency graph of tasks. Planning never executes paid calls or contact."""
    tasks = []
    def task(name, requires, ready, reason):
        completed = {x['name'] for x in tasks if x['status']=='completed'}
        dependencies_ready = set(requires).issubset(completed)
        tasks.append(dict(name=name, requires=requires,
                          status=('completed' if ready else 'waiting') if dependencies_ready else 'blocked',
                          evidence_ready=ready, reason=reason))
    task('acquisition_and_anomalies', [], review['anomaly']['status']=='completed', review['anomaly']['blocking_reasons'])
    task('complete_identity_and_comparables', ['acquisition_and_anomalies'], not review['comparable_audit']['blocking_reasons'], review['comparable_audit']['blocking_reasons'])
    task('technical_risk_and_damage', ['complete_identity_and_comparables'], review['technical_risk']['status']=='completed', review['technical_risk']['blocking_reasons'])
    task('parts_labor_and_other_costs', ['technical_risk_and_damage'], review['economics']['costs_complete'] and review['repair_planning']['status']=='completed', review['economics']['blocking_reasons']+review['repair_planning']['blocking_reasons'])
    task('conservative_economics', ['parts_labor_and_other_costs'], not review['economics']['blocking_reasons'], review['economics']['blocking_reasons'])
    task('independent_finalist_review', ['conservative_economics'], review['approved_for_final_checks'], review['blocking_reasons'])
    task('calibrated_resale_and_supervisor', ['independent_finalist_review'], calibrated, ['Calibrated resale and liquidity evidence required'])
    task('publication', ['calibrated_resale_and_supervisor'], False, ['Recheck source and enforce publication policy'])
    completed = {x['name'] for x in tasks if x['status']=='completed'}
    ready = [x['name'] for x in tasks if x['status']=='waiting' and set(x['requires']).issubset(completed)]
    return dict(version=VERSION, route='verification' if selected else 'enrichment', tasks=tasks,
                next_ready_tasks=ready if selected else ['complete_source_fields_and_rescreen'],
                automatic_paid_calls=False, seller_contact_enabled=False,
                existing_queue_is_authority=True, new_snapshot_requires_new_review=True)
