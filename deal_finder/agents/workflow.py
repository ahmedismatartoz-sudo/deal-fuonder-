"""Seven product components; deterministic controls retain their audited results."""
from ..models import Listing
from .contracts import PIPELINE_VERSION
from .specialists import analyze as controls, selection_decision
from .contracts import Result
from .market_experts import review as market_review, registry as expert_registry
from .handoff import parts_handoff, card, FILTER_FIELDS
from .photo_identity import execute as identify_photos
from .professional import (AnnouncementAnomalyAgent, IndependentReviewAgent, POLICY,
                           registry as task_registry, coordinate)
import os


COMPONENTS = (
    ('collection', (), 'Archive source originals, quarantine and resumable collection checkpoints.'),
    ('market_selection', ('collection',), 'Compare like-for-like cars and screen 1000..50000 EUR candidates.'),
    ('repairs', ('market_selection',), 'Research compatible parts prices and separate hours; preserve legacy complete quote controls.'),
    ('resale', ('repairs',), 'Provide resale estimates only after transaction-model calibration.'),
    ('opportunity', ('resale',), 'Account for all costs, forecast profit and documented sale liquidity.'),
    ('supervisor', ('market_selection', 'repairs', 'resale', 'opportunity'), 'Aggregate evidence, calibration and availability gates.'),
    ('publication', ('supervisor',), 'Publish only supervisor-approved, current opportunities.')
)


def registry():
    parts_roles = [dict(name=name, version=PIPELINE_VERSION, purpose=purpose) for name, purpose in (
        ('damage_scope', 'Separate diagnosed repairs from parts hypotheses and hidden-damage questions.'),
        ('parts_fitment', 'Check codes, variant, side, dimensions and required kit contents.'),
        ('parts_web_research', 'Search supplier and manufacturer pages with cited observations and bounded provider requests.'),
        ('parts_basket', 'Normalize pack quantities, seller aliases, observed prices and known delivery/deposit charges.'),
        ('operation_hours', 'Report sourced time ranges separately, without a labor price or blind summation.'))]
    parts_roles.append(dict(name='technical_risk', version=PIPELINE_VERSION,
                            purpose='Escalate severe or unknown collision scope and require variant-specific evidence.'))
    return [dict(name=name, version=PIPELINE_VERSION, requires=list(requires),
                 mode='collection' if name == 'collection' else 'analysis', purpose=purpose,
                 subagents=(expert_registry()+[
                     dict(name='family_price_triage', version=PIPELINE_VERSION, purpose='Prioritize incomplete source observations using provisional family amounts.'),
                     dict(name='archive_enrichment', version=PIPELINE_VERSION, purpose='Persist missing-field plans before exact price selection.'),
                     dict(name='photo_web_identity', version=PIPELINE_VERSION, purpose='Combine photos, ad and web references; expose uncertain variants'),
                     dict(name='announcement_anomalies', version=PIPELINE_VERSION, purpose='Flag conditional prices, documents and identity contradictions')]
                     if name == 'market_selection' else parts_roles if name == 'repairs' else
                     [dict(name='independent_review', version=PIPELINE_VERSION,
                           purpose='Recompute stressed economics, audit evidence and require a distinct reviewer')]
                     if name == 'supervisor' else []),
                 concrete_tasks=[item for item in task_registry()
                                 if item['name'] in {'collection':['collection'], 'market_selection':['identity','market'],
                                 'repairs':['damage','parts','labor'], 'resale':['resale'], 'opportunity':['opportunity'],
                                 'supervisor':['supervisor','evaluation'], 'publication':['publication']}[name]])
            for name, requires, purpose in COMPONENTS]


def analyze(raw, candidates, as_of, **kwargs):
    photo = {}
    def before_checks(ctx):
        selected = selection_decision(ctx.target, ctx.results.get('market'))['candidate']
        anomaly = AnnouncementAnomalyAgent().execute(raw, ctx.target, candidates, as_of)
        if anomaly['data']['hard_blockers']:
            photo.update(status='blocked', reason='Resolve announcement anomalies before further research',
                         identity_attestation=False, exact_part_fitment_confirmed=False)
            return anomaly['blocking_reasons']
        if selected and os.getenv('DEAL_FINDER_PHOTO_IDENTITY_ENABLED') == '1':
            photo.update(identify_photos(raw, as_of))
        else:
            photo.update(status='waiting' if selected else 'blocked',
                         reason='Photo/web identification requires selected candidate and configured adapter',
                         identity_attestation=False, exact_part_fitment_confirmed=False)
        return ['Resolve photo/listing specification conflicts before further checks'] if photo.get('conflicting_fields') else []
    out = controls(raw, candidates, as_of, before_checks=before_checks, **kwargs)
    quality = out['quality']
    target = Listing.parse(quality['data']['listing']) if quality['status'] == 'completed' else None
    market = out['market']
    decision = selection_decision(target, Result('market', market['status'], market['data'], market['reasons']))
    selected = decision['candidate']
    out['photo_identity'] = photo
    photo_conflicts = out['photo_identity'].get('conflicting_fields', [])
    if photo_conflicts:
        selected = False
        decision = dict(decision, candidate=False, route='enrichment', reason='Photo and listing identity conflict')
        validation = out['validation']['data']
        validation.update(analysis_state='needs_evidence', scenario_ready=False)
        validation['missing_or_blocked']['photo_identity'] = ['Resolve photo/listing specification conflicts']
    pre_anomaly = AnnouncementAnomalyAgent().execute(raw, target, candidates, as_of)
    if pre_anomaly['data']['hard_blockers']:
        selected = False
        decision = dict(decision, candidate=False, route='enrichment', reason='Announcement anomalies require review')
        out['validation']['data'].update(analysis_state='needs_evidence', scenario_ready=False)
        out['validation']['data']['missing_or_blocked']['announcement_anomalies'] = pre_anomaly['blocking_reasons']
    repaired = selected and out['identity']['status'] == 'completed' and out['condition']['status'] == 'completed' and out['repair']['status'] == 'completed'
    checks = dict(valid_listing=target is not None, identity_verified=out['identity']['status'] == 'completed',
                  selected_by_price=selected, photo_identity_consistent=not bool(photo_conflicts), repairs_documented=repaired,
                  all_costs_documented=out['opportunity']['status'] == 'completed',
                  calibrated_resale_model=False, calibrated_sale_time_model=False,
                  acquisition_terms_verified=False, current_source_availability_verified=False)
    blocked = [name for name, passed in checks.items() if not passed]
    components = {
        'collection': dict(status='completed', data=dict(input_received=True, live_scraping_confirmed=False)),
        'market_selection': dict(status='completed' if market['status'] == 'completed' else 'blocked',
                                 data=dict(**decision, benchmark=market['data'])),
        'repairs': dict(status='completed' if repaired else 'blocked', data=out['repair']['data'],
                        reasons=out['condition']['reasons'] + out['repair']['reasons'] +
                        (['Resolve photo/listing identity conflict before repairs'] if photo_conflicts else [])),
        'resale': dict(status='blocked', data=dict(recommended_price_eur=None, prediction_interval_eur=None,
                       model_version=None), reasons=['Transaction-based resale model is not calibrated.']),
        'opportunity': dict(status='blocked', data=dict(scenario=out['opportunity']['data'],
                            forecast_profit_cents=None, forecast_profit_low_cents=None, expected_days_to_sell=None, popularity_score=None,
                            liquidity_basis='unavailable_without_documented_outcomes'),
                            reasons=['Calibrated resale and liquidity estimates required.']),
        'supervisor': dict(status='completed', data=dict(approved=False, checks=checks, blocking_checks=blocked)),
        'publication': dict(status='blocked', data=dict(publishable=False),
                            reasons=['Supervisor has not approved an opportunity.'])
    }
    pool = kwargs.get('source_asking_candidates')
    pool = candidates if pool is None else pool
    out['market_experts'] = market_review(target, pool, as_of, decision)
    from .damage_scope import execute as damage_scope
    out['damage_scope'] = damage_scope(raw, selected, out['condition'])
    parts = parts_handoff(raw, target, selected, as_of)
    out['parts_research'] = parts
    out['candidate_card'] = card(target, decision, market['data'], parts)
    from ..technical_web import configured as risk_web_configured, execute as risk_web_research
    from .professional import damage_severity
    if selected and risk_web_configured() and not photo_conflicts:
        out['technical_web_research'] = risk_web_research(target, damage_severity(raw,target,as_of),as_of)
    else:
        out['technical_web_research'] = dict(status='configuration_required' if selected else 'blocked',
                                            verified_on_vehicle=False,automatic_paid_retry=False)
    review = IndependentReviewAgent().execute(raw, target, market['data'], out['repair'],
                                            out['opportunity'], pool, as_of)
    out['announcement_anomalies'] = review['anomaly']
    out['technical_risk'] = review['technical_risk']
    out['technical_risk']['research_hypotheses'] = out['technical_web_research']
    out['repair_planning'] = review['repair_planning']
    out['independent_review'] = review
    out['coordinator'] = coordinate(review, selected=selected)
    checks.update(announcement_verified=review['anomaly']['status']=='completed',
                  technical_risk_resolved=review['technical_risk']['status']=='completed',
                  strict_comparables_verified=not review['comparable_audit']['blocking_reasons'],
                  conservative_margin_at_least_2000=review['economics']['passes_margin'],
                  conservative_margin_meets_price_band=review['economics']['passes_margin'],
                  conservative_return_passed=review['economics']['passes_return'],
                  simultaneous_adverse_scenario_passed=review['economics']['passes_resilience'],
                  independent_review_passed=review['approved_for_final_checks'])
    components['supervisor']['data'].update(blocking_checks=[name for name, passed in checks.items() if not passed],
                                          professional_policy=dict(POLICY),
                                          professional_blocking_reasons=review['blocking_reasons'])
    components['opportunity']['data']['conservative_scenario'] = review['economics']
    if out['candidate_card'] is not None:
        economics = review['economics']
        out['candidate_card'].update(conservative_margin_low_cents=economics['margin_low_cents'],
                                     maximum_offer_cents=economics['maximum_offer_cents'],
                                     total_investment_cents=economics['total_investment_cents'],
                                     minimum_required_margin_cents=economics['minimum_margin_cents'],
                                     severe_controls_required=review['technical_risk']['data']['severe_controls_required'],
                                     professional_policy_ready=review['approved_for_final_checks'],
                                     opportunity_status='research_only', high_opportunity_approved=False,
                                     policy_blocking_reasons=review['blocking_reasons'])
    out['handoff'] = dict(route=decision['route'], tasks=list(dict.fromkeys(out['coordinator']['next_ready_tasks']+out['market_experts']['next_tasks'])),
                          ordered_stages=['source_quality', 'announcement_anomalies', 'market_selection',
                                          'vehicle_identity', 'technical_risk', 'damage_scope',
                                          'parts_prices', 'operation_hours', 'other_costs',
                                          'independent_review', 'supervisor', 'publication'],
                          filter_fields=list(FILTER_FIELDS), shared_archive_required=True,
                          price_lookup_policy='free_only', paid_lookup_enabled=False)
    out['components'] = components
    if '_collection_audit' in raw:
        out['collection_audit'] = raw['_collection_audit']
    out['pipeline_version'] = PIPELINE_VERSION
    return out
