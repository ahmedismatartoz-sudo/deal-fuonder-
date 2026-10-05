"""Provisional parts estimates from externally researched offers, never a diagnosis.

No network is performed here. A web-search adapter supplies dated observations.
Intervals are engineering envelopes, not calibrated confidence intervals.
"""
from collections import defaultdict
from datetime import datetime, timezone
from math import ceil
from statistics import median
from urllib.parse import urlsplit

VERSION = 'parts-web-research-v1'


def number(value, name, maximum=10_000_000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(name + ' must be a nonnegative bounded integer')
    return value


def stamp(value):
    date = datetime.fromisoformat(value)
    if date.tzinfo is None:
        raise ValueError('Observation requires timezone')
    return date.astimezone(timezone.utc)


def url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Evidence requires a public HTTPS URL')
    return value


def search_plan(vehicle, part):
    """Ask by exact code first, otherwise expose unresolved fitment dimensions."""
    fields = ('make', 'model', 'generation', 'year', 'engine_code', 'gearbox')
    identity = ' '.join(str(vehicle[k]) for k in fields if vehicle.get(k))
    description = str(part['name'])
    constraints = ' '.join(f'{k} {v}' for k, v in part.get('requirements', {}).items())
    code = part.get('part_number', '')
    query = ' '.join(str(x) for x in (code, identity, description, constraints, 'prezzo Italia') if x)
    return dict(version=VERSION, queries=[f'site:{site} {query}' for site in
                ('auto-doc.it', 'mister-auto.it', 'norauto.it', 'motointegrator.it')],
                exact_code_query=f'"{code}" prezzo Italia' if code else None,
                missing_vehicle_fields=[k for k in fields if not vehicle.get(k)],
                checks=['product page, not only search snippet', 'OEM cross-reference or vehicle fitment',
                        'side, dimensions, lighting, connectors and kit contents',
                        'seller, stock, VAT, quantity per price, shipping and bulky surcharge',
                        'separate OE / aftermarket / used and independent sellers'],
                stop_on_source_denial=True)


def estimate_parts(request, *, as_of=None):
    as_of = stamp(as_of) if as_of else datetime.now(timezone.utc)
    vehicle = request.get('vehicle', {})
    parts = request['parts']
    if not isinstance(vehicle, dict) or not isinstance(parts, list) or not 1 <= len(parts) <= 100:
        raise ValueError('Require vehicle and 1..100 parts')
    if len({p['id'] for p in parts}) != len(parts):
        raise ValueError('Duplicate required part IDs')
    results = []
    for part in parts:
        quantity = number(part['quantity'], 'quantity', 100)
        if not quantity:
            raise ValueError('Quantity must be positive')
        fallback = part['fallback']
        low = number(fallback['low_cents'], 'fallback low')
        high = number(fallback['high_cents'], 'fallback high')
        if not high or high < low or not fallback.get('basis') or not fallback.get('source_urls'):
            raise ValueError('Fallback requires ordered bounds, explicit basis and sources')
        for source in fallback['source_urls']:
            url(source)
        requirements = part.get('requirements', {})
        groups, rejected, seen = defaultdict(list), [], set()
        offers = part.get('offers', [])
        if not isinstance(offers,list):
            raise ValueError('Offers must be an array')
        for offer in offers:
            if not isinstance(offer,dict):
                rejected.append(dict(url=None,reason='Offer must be an object'))
                continue
            try:
                url(offer['url'])
                age = (as_of-stamp(offer['observed_at'])).total_seconds()/86400
                if not 0 <= age <= 14:
                    raise ValueError('stale or future observation')
                if offer['currency'] != 'EUR' or offer['vat_included'] is not True:
                    raise ValueError('currency or VAT unresolved')
                if offer['availability'] != 'available':
                    raise ValueError('stock not confirmed in source')
                if offer['condition'] != part['condition']:
                    raise ValueError('different condition')
                if offer['tier'] != part['tier']:
                    raise ValueError('different OE/aftermarket tier')
                if not offer.get('fitment_basis') or any(offer.get('attributes', {}).get(k) != v
                                                        for k, v in requirements.items()):
                    raise ValueError('incomplete or conflicting compatibility')
                price = number(offer['price_cents'], 'price')
                pack = number(offer['pack_quantity'], 'pack quantity', 100)
                if not price or not pack:
                    raise ValueError('zero price or unknown pack quantity')
                domain = urlsplit(offer['url']).hostname.removeprefix('www.')
                group = offer['seller_group'].strip().casefold()
                if not group:
                    raise ValueError('seller group missing')
                # Known storefront aliases must not inflate independent evidence.
                if domain in ('auto-doc.it', 'autoparti.it', 'tuttoautoricambi.it') and offer.get('seller') == 'AUTODOC':
                    group = 'autodoc'
                key = (group, offer['brand'].casefold(), offer['part_number'].strip().casefold())
                if key in seen:
                    raise ValueError('duplicate seller and product')
                seen.add(key)
                groups[group].append(dict(offer, normalized_cost_cents=ceil(quantity/pack)*price))
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                rejected.append(dict(url=offer.get('url'), reason=str(error)))
        accepted = [offer for group in groups.values() for offer in group]
        costs = [o['normalized_cost_cents'] for o in accepted]
        # One seller gets one vote regardless of how many brands it lists.
        typical = round(median([median([o['normalized_cost_cents'] for o in group])
                                for group in groups.values()])) if costs else (low+high)//2
        uncertain = (len(groups) < 2 or any(o.get('retrieval') != 'live_page' for o in accepted)
                     or part.get('diagnosis_confirmed') is not True
                     or part.get('identity_confirmed') is not True)
        if costs:
            observed_low, observed_high = min(costs), max(costs)
            if uncertain:
                low, high = min(low, observed_low), max(high, observed_high)
            else:
                low, high = observed_low, observed_high
        hours = part['hours']
        hour_low = number(hours['low_minutes'], 'hours low', 100000)
        hour_high = number(hours['high_minutes'], 'hours high', 100000)
        if hour_high < hour_low or not hours.get('basis'):
            raise ValueError('Hours require explicit basis and ordered bounds')
        typical = max(low, min(typical, high))
        results.append(dict(id=part['id'], name=part['name'], quantity=quantity,
                            parts_low_cents=low, parts_typical_cents=typical, parts_high_cents=high,
                            basis='observed_offers_with_assumptions' if costs else 'sourced_analogy',
                            uncertainty='wide' if uncertain or not costs else 'observed_price_range',
                            independent_sellers=len(groups), accepted_offers=accepted,
                            rejected_offers=rejected, assumptions=fallback['basis'],
                            fallback_sources=fallback['source_urls'], hours=hours,
                            shipping_included=False))
    return dict(version=VERSION, status='provisional', vehicle=vehicle, items=results,
                parts_low_cents=sum(x['parts_low_cents'] for x in results),
                parts_typical_cents=sum(x['parts_typical_cents'] for x in results),
                parts_high_cents=sum(x['parts_high_cents'] for x in results),
                labor_cost_cents=None, hours_additive=False,
                calibrated=False, verified_quote=False, forecasts_enabled=False,
                exclusions=['shipping and bulky surcharges', 'paint and consumables unless explicitly listed',
                            'unidentified hidden damage; upper bound is not a guaranteed maximum'],
                as_of=as_of.isoformat())
