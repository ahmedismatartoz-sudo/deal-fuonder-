"""Reviewed nearby provinces; published city matched against an ISTAT snapshot."""
import json
import re
import unicodedata
from functools import lru_cache
from importlib.resources import files

GEOGRAPHY_VERSION = 'milano-nearby-provinces-v1'


def normalized(value):
    value = unicodedata.normalize('NFKD', value.casefold().replace('’', "'"))
    return re.sub(r'\s+', ' ', ''.join(c for c in value if not unicodedata.combining(c))).strip()


@lru_cache(maxsize=1)
def registry():
    data = json.loads(files('deal_finder').joinpath('data/nearby_municipalities.json').read_text())
    cities = {}
    for city, province in data['municipalities']:
        cities.setdefault(normalized(city), []).append((city, province))
    cities['milan'] = cities['milano']
    provinces = {normalized(label): code for code, (label, _) in data['provinces'].items()}
    provinces.update({code.casefold(): code for code in data['provinces']})
    provinces['monza e brianza'] = 'MB'
    return data, cities, provinces


def scope():
    data, _, _ = registry()
    return dict(version=GEOGRAPHY_VERSION, provinces=data['allowed_provinces'],
                municipality_registry_date=data['reference_date'])


def published_location(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Published nearby municipality required')
    data, cities, provinces = registry()
    parts = [normalized(p) for p in value.split(',')]
    city = parts[0]
    match = re.fullmatch(r'(.+?)\s*\(([a-z]{2})\)', city)
    labels = parts[1:]
    if match:
        city = match[1].strip()
        labels.append(match[2])
    candidates = cities.get(city, [])
    if not candidates:
        raise ValueError('Published municipality outside approved nearby provinces')
    hints = {provinces[p] for p in labels if p in provinces}
    if len(hints) > 1:
        raise ValueError('Conflicting published provinces')
    if hints:
        candidates = [c for c in candidates if c[1] in hints]
    elif city in data['ambiguous_names']:
        raise ValueError('Ambiguous municipality requires published province')
    if len(candidates) != 1:
        raise ValueError('Published municipality and province conflict')
    canonical_city, province = candidates[0]
    region = normalized(data['provinces'][province][1])
    regions = {normalized(v[1]) for v in data['provinces'].values()}
    regions.update({'lombardy', 'piedmont'})
    aliases = {'lombardy': 'lombardia', 'piedmont': 'piemonte'}
    if any(aliases.get(p, p) != region for p in labels if p in regions):
        raise ValueError('Published municipality and region conflict')
    return dict(city=canonical_city, province=province,
                location_basis='provider_published_city_matched_istat_registry',
                collection_geography=GEOGRAPHY_VERSION)
