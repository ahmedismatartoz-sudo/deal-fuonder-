"""Recover explicit Facebook seller evidence without rewriting the archive.

Title year remains a seller claim; raw provider car_miles never becomes km.
Missing price terms and condition are not silently certified.
"""
import re
from copy import deepcopy
from .vehicle_searches import title_identity
from .vehicle_identity import enrich, normalize_field

VERSION = 'facebook-seller-evidence-v1'
NUMBER = r'(?:\d{1,3}(?:[ .]\d{3})+|\d{4,7})'


def mileage_claims(description):
    found = []
    patterns = (rf'(?<!\d)({NUMBER})\s*(?:km|chilometri|kilometri)\b',
                rf'\b(?:km|chilometri|kilometri)\s*[:=]?\s*({NUMBER})(?!\d|[.,]\d)',
                r'(?<!\d)(\d{1,3})\s*(?:mila|[kK])\s*(?:km|chilometri)\b')
    for i, pattern in enumerate(patterns):
        for match in re.finditer(pattern, description, re.I):
            # Service/distribution milestones do not describe today's odometer.
            prefix = description[max(0, match.start()-70):match.start()]
            if re.search(r'(?:distribuzion\w*|cinghia|tagliando|sostituit\w*|cambiat\w*|motore rifatto|batteria).{0,45}$', prefix, re.I):
                continue
            value = int(re.sub(r'[ .]', '', match[1])) * (1000 if i == 2 else 1)
            if 0 <= value <= 1000000:
                found.append(dict(value=value, excerpt=match[0], origin='seller_description'))
    return list({(p['value'], p['excerpt']): p for p in found}.values())


def recover(payload, *, source_url=None):
    value = deepcopy(payload)
    original = payload.get('original') or {}
    description = str(payload.get('description') or original.get('description') or '')
    title = str(payload.get('title') or original.get('title') or '')
    identity = title_identity(title)
    claims, conflicts = {}, []
    if identity:
        for field in ('make', 'model'):
            old = value.get(field)
            corrected_family = (field == 'model' and old == '500'
                                and identity[field] in ('500L', '500X'))
            if old and old != identity[field] and not corrected_family:
                conflicts.append(dict(field=field, values=[old, identity[field]]))
            if not old or corrected_family:
                value[field] = identity[field]
            claims[field] = dict(value=identity[field], origin='seller_title', excerpt=title)
        if value.get('year') is None:
            value['year'] = identity['model_year_from_title']
            claims['year'] = dict(value=value['year'], origin='seller_title_model_year',
                                  registration_verified=False, excerpt=title)
    kilometres = mileage_claims(description)
    km_values = {p['value'] for p in kilometres}
    existing = value.get('mileage_km')
    if type(existing) is int:
        km_values.add(existing)
    if len(km_values) == 1:
        value['mileage_km'] = next(iter(km_values))
        claims['mileage_km'] = dict(value=value['mileage_km'], origin='explicit_published_km',
                                    excerpts=[p['excerpt'] for p in kilometres])
    elif len(km_values) > 1:
        value['mileage_km'] = None
        conflicts.append(dict(field='mileage_km', values=sorted(km_values)))
    else:
        value['mileage_km'] = None
    # Keep explicit labels rather than guessing fuel from the usual engine.
    text = title+' '+description
    fuel_claims = set()
    for fuel, pattern in (('diesel',r'\b(?:diesel|gasolio)\b'),
                          ('petrol',r'\b(?:benzina|petrol|gasoline)\b'),
                          ('lpg',r'\bgpl\b'),('cng',r'\bmetano\b'),
                          ('electric',r'\b(?:elettrica|elettrico|electric)\b')):
        for match in re.finditer(pattern, text, re.I):
            if not re.search(r'\b(?:non|no|senza)\s*$', text[max(0,match.start()-20):match.start()], re.I):
                fuel_claims.add(fuel)
    if fuel_claims in ({'petrol','lpg'}, {'petrol','cng'}):
        fuel_claims = {'petrol_lpg' if 'lpg' in fuel_claims else 'petrol_cng'}
    if value.get('fuel'):
        fuel_claims.add(normalize_field('fuel', value['fuel']))
    if len(fuel_claims) == 1:
        value['fuel'] = next(iter(fuel_claims))
        claims['fuel'] = dict(value=value['fuel'], origin='explicit_seller_label')
    elif len(fuel_claims) > 1:
        value['fuel'] = None
        conflicts.append(dict(field='fuel', values=sorted(fuel_claims)))
    if not value.get('transmission') and original.get('transmission'):
        value['transmission'] = original['transmission']
    value = enrich(value, source_url=source_url)
    conflicts += value['identity_dossier']['conflicts']
    value['facebook_evidence'] = dict(version=VERSION, claims=claims, conflicts=conflicts,
        raw_provider_car_miles=original.get('car_miles'), provider_mileage_units_verified=False,
        registration_year_verified=False, physical_identity_verified=False,
        total_price_verified=False, condition_verified=False)
    return value
