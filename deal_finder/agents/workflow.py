"""Seven product components; deterministic controls retain their audited results."""
from ..models import Listing
from .contracts import PIPELINE_VERSION
from .specialists import analyze as controls, selection_decision
from .contracts import Result
from .market_experts import review, registry as expert_registry
from .handoff import parts_handoff, card, FILTER_FIELDS
from .photo_identity import execute as identify_photos
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
    return [dict(name=name, version=PIPELINE_VERSION, requires=list(requires),
                 mode='collection' if name == 'collection' else 'analysis', purpose=purpose,
                 subagents=expert_registry()+[dict(name='photo_web_identity', version=PIPELINE_VERSION, purpose='Combine photos, ad and web references; expose uncertain variants')] if name == 'market_selection' else [])
            for name, requires, purpose in COMPONENTS]


def analyze(raw, candidates, as_of, **kwargs):
    out = controls(raw, candidates, as_of, **kwargs)
    quality = out['quality']
    target = Listing.parse(quality['data']['listing']) if quality['status'] == 'completed' else None
    market = out['market']
    decision = selection_decision(target, Result('market', market['status'], market['data'], market['reasons']))
    selected = decision['candidate']
    if selected and os.getenv('DEAL_FINDER_PHOTO_IDENTITY_ENABLED') == '1':
        out['photo_identity'] = identify_photos(raw, as_of)
    else:
        out['photo_identity'] = dict(status='waiting' if selected else 'blocked',
                                    reason='Photo/web identification requires selected candidate and configured adapter',
                                    identity_attestation=False, exact_part_fitment_confirmed=False)
    photo_conflicts = out['photo_identity'].get('conflicting_fields', [])
    if photo_conflicts:
        selected = False
        decision = dict(decision, candidate=False, route='enrichment', reason='Photo and listing identity conflict')
        validation = out['validation']['data']
        validation.update(analysis_state='needs_evidence', scenario_ready=False)
        validation['missing_or_blocked']['photo_identity'] = ['Resolve photo/listing specification conflicts']
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
                            forecast_profit_cents=None, expected_days_to_sell=None, popularity_score=None,
                            liquidity_basis='unavailable_without_documented_outcomes'),
                            reasons=['Calibrated resale and liquidity estimates required.']),
        'supervisor': dict(status='completed', data=dict(approved=False, checks=checks, blocking_checks=blocked)),
        'publication': dict(status='blocked', data=dict(publishable=False),
                            reasons=['Supervisor has not approved an opportunity.'])
    }
    pool = kwargs.get('source_asking_candidates')
    pool = candidates if pool is None else pool
    out['market_experts'] = review(target, pool, as_of, decision)
    parts = parts_handoff(raw, target, selected, as_of)
    out['parts_research'] = parts
    out['candidate_card'] = card(target, decision, market['data'], parts)
    out['handoff'] = dict(route=decision['route'], tasks=out['market_experts']['next_tasks'],
                          filter_fields=list(FILTER_FIELDS), shared_archive_required=True,
                          price_lookup_policy='free_only', paid_lookup_enabled=False)
    out['components'] = components
    out['pipeline_version'] = PIPELINE_VERSION
    return out
