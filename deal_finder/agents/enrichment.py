"""Organize incomplete source observations before any repair-price research."""
from dataclasses import fields
from ..models import Listing
from .photo_identity import plan

VERSION = 'archive-enrichment-v1'


def listing_input(source, source_id, observed_at, url, payload):
    allowed = {field.name for field in fields(Listing)}
    value = {key: value for key, value in payload.items() if key in allowed}
    value.update(source=source, source_id=source_id, observed_at=observed_at, url=url)
    return value


def execute(raw, db, as_of):
    listing = raw['listing']
    latest = db.execute('SELECT observed_at, active FROM listing_events WHERE source=? AND source_id=? ORDER BY observed_at DESC LIMIT 1',
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
    return dict(enrichment=value, pipeline_version=VERSION,
                validation=dict(data=dict(analysis_state=state, scenario_ready=False,
                                          forecast_ready=False, buy_recommendation=False)))
