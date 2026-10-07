"""Organize incomplete source observations before any repair-price research."""
from dataclasses import fields
import os
from ..models import Listing
from .photo_identity import plan

VERSION = 'archive-enrichment-identity-damage-v3'


def listing_input(source, source_id, observed_at, url, payload):
    allowed = {field.name for field in fields(Listing)}
    value = {key: value for key, value in payload.items() if key in allowed}
    value.update(source=source, source_id=source_id, observed_at=observed_at, url=url)
    return value


def execute(raw, db, as_of):
    listing = raw['listing']
    latest = db.execute('SELECT observed_at, active, url, payload FROM listing_events WHERE source=? AND source_id=? ORDER BY observed_at DESC LIMIT 1',
                        (listing['source'], listing['source_id'])).fetchone()
    state = 'needs_evidence'
    if not latest or latest[0] != listing['observed_at'] or not latest[1]:
        state = 'superseded'
    value = dict(agent='archive_enrichment', version=VERSION, status=state,
                 source=listing['source'], source_id=listing['source_id'],
                 observed_at=listing['observed_at'], publishable=False,
                 identity_attestation=False, automatic_paid_calls=False,
                 repair_search_started=False, tasks=[], missing_fields=[])
    if state == 'superseded':
        value['reason'] = 'A newer source observation supersedes this enrichment task.'
    else:
        # Reinterpret retained originals with the corrected adapter. This does
        # not fetch a new page, spend credits, or overwrite source history.
        payload = db.json_decode(latest[3])
        if listing['source'] == 'autoscout24' and isinstance(payload.get('original'), dict):
            from ..autoscout24 import record_event
            recovered = record_event(payload['original'], url=latest[2],
                observed_at=listing['observed_at'], detailed=payload.get('detail_fetched') is True,
                original_title=payload.get('title'))['payload']
            listing = listing_input(listing['source'],listing['source_id'],listing['observed_at'],latest[2],
                                    dict(payload, **recovered))
        if not listing.get('province') and listing.get('city'):
            from ..collection_geography import published_location
            try:
                listing['province'] = published_location(listing['city'])['province']
            except ValueError:
                pass
        value['resolved_listing'] = listing
        from ..vehicle_identity import enrich
        listing=enrich(listing,source_url=listing.get('url'))
        value['resolved_listing']=listing
        value['identity_dossier']=listing['identity_dossier']
        from ..damage_screening import classify
        value['damage_screening']=classify(listing)
        from .market_triage import review
        value['market_triage'] = review(listing, db, as_of)
        value['priority'] = 'price_signal' if value['market_triage']['priority_enrichment'] else 'data_completion'
        required = ('make', 'model', 'generation', 'trim', 'fuel', 'transmission', 'year',
                    'mileage_km', 'province', 'seller_type')
        value['missing_fields'] = [key for key in required if listing.get(key) is None
                                  or str(listing[key]).strip().casefold() in ('', 'unknown', 'n/a', '-')]
        value['invalid_fields'] = []
        for key in required:
            if key in value['missing_fields']:
                continue
            field = listing[key]
            valid = (not isinstance(field, bool) and str(field).isdigit()) if key in ('year', 'mileage_km') else isinstance(field, str)
            if not valid:
                value['invalid_fields'].append(key)
        if listing.get('price_kind') != 'total':
            value['missing_fields'].append('total_asking_price')
        if listing.get('condition', 'unknown') == 'unknown':
            value['missing_fields'].append('condition')
        try:
            value['identity_plan'] = plan({'listing': listing})
        except (ValueError, TypeError, AttributeError):
            value['identity_plan'] = None
            value['missing_fields'].append('valid_photo_or_identity_sources')
        value['tasks'] = [dict(name='resolve_source_fields', requires=[], fields=value['missing_fields']+value['invalid_fields']),
                          dict(name='rescreen_market', requires=['resolve_source_fields']),
                          dict(name='verify_identity_and_damage', requires=['price_selection']),
                          dict(name='research_parts_web', requires=['price_selection', 'identified_variant', 'required_parts'])]
        value['reason'] = 'Source evidence or reviewed enrichment is required; unknown fields remain unknown.'
        from ..price_memory import risky,research_policy
        if listing['identity_dossier']['conflicts']:
            value['research_execution']=dict(status='needs_identity_review',conflicts=listing['identity_dossier']['conflicts'])
            value['tasks'].insert(0,dict(name='resolve_identity_conflicts',requires=[],conflicts=listing['identity_dossier']['conflicts']))
        elif (not value['damage_screening']['eligible_for_opportunity_research'] if research_policy()['profile']=='opportunities' else risky(listing)):
            value['research_execution'] = dict(status='blocked', reason='Severe or insufficiently described damage excluded')
        elif not value['market_triage']['priority_enrichment']:
            value['research_execution'] = dict(status='blocked', reason='Comparable price signal required before external research')
        else:
            from ..agent_runtime import connections
            ready = connections()
            if not ready['photo_web_provider_configured']:
                value['research_execution'] = dict(status='configuration_required',
                    missing_configuration=[key for key,present in (
                        ('OPENAI_API_KEY',ready['api_key_present']),
                        ('DEAL_FINDER_VISION_MODEL',ready['vision_model_present'])) if not present],
                    provider_enabled=os.getenv('DEAL_FINDER_PHOTO_IDENTITY_ENABLED')=='1')
            else:
                from .photo_identity import execute as identify
                value['automatic_paid_calls'] = True
                value['identity_research'] = identify(dict(listing=listing),as_of)
                value['research_execution'] = dict(status=value['identity_research']['status'],
                    evidence_persisted=True, identity_attestation=False,
                    next_stage='review_variant_and_inspection_scope')
    return dict(enrichment=value, pipeline_version=VERSION,
                validation=dict(data=dict(analysis_state=state, scenario_ready=False,
                                          forecast_ready=False, buy_recommendation=False)))
