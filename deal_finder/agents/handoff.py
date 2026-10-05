"""Parts-only handoff and searchable candidate cards; never net-profit claims."""
from ..repair_research import estimate_parts
from ..models import normalize

FILTER_FIELDS = ('make', 'model', 'generation', 'price_eur', 'mileage_km', 'year', 'fuel',
                 'transmission', 'province', 'seller_type', 'condition', 'route',
                 'parts_low_cents', 'parts_high_cents', 'potential_gross_low_cents',
                 'potential_gross_high_cents', 'potential_gross_low_percent', 'observed_at')


def parts_handoff(raw, target, selected, as_of):
    result = dict(status='waiting', data=dict(labor_cost_cents=None, labor_excluded=True,
                  calibrated=False, search_adapter_connected=False), reasons=[])
    if not selected:
        result.update(status='blocked', reasons=['Candidate screening precedes parts research.'])
        return result
    request = raw.get('parts_research')
    if request is None:
        result['reasons'] = ['Await identified vehicle, diagnosed parts and sourced price observations.']
        return result
    try:
        vehicle = request['vehicle']
        if vehicle.get('vehicle_id') != target.vehicle_id or not target.vehicle_id:
            raise ValueError('Parts research must identify the same vehicle')
        for key in ('make', 'model', 'generation'):
            if normalize(vehicle[key]) != getattr(target, key):
                raise ValueError('Parts research vehicle specification conflict: ' + key)
        if vehicle['year'] != target.year or normalize(vehicle['gearbox']) != target.transmission:
            raise ValueError('Parts research year or gearbox mismatch')
        value = estimate_parts(request, as_of=as_of.isoformat())
        result.update(status='provisional', data=dict(value, search_adapter_connected=False))
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
        result.update(status='needs_review', reasons=[str(error)])
    return result


def card(target, decision, market, parts):
    if target is None:
        return None
    value = {key: getattr(target, key) for key in FILTER_FIELDS if hasattr(target, key)}
    value.update(source=target.source, source_id=target.source_id, url=target.url,
                 title=target.title, description=target.description, image_urls=target.image_urls,
                 route=decision['route'], publishable=False, evidence_confidence='uncalibrated',
                 parts_low_cents=None, parts_high_cents=None,
                 potential_gross_low_cents=None, potential_gross_high_cents=None, potential_gross_low_percent=None,
                 labor_cost_cents=None, hours=None, hours_additive=False, margin_basis='before_labor_and_other_costs')
    if parts['status'] == 'provisional':
        data = parts['data']
        totals = dict(low=data['parts_low_cents'], high=data['parts_high_cents'])
        value.update(parts_low_cents=totals['low'], parts_high_cents=totals['high'],
                     hours=[dict(repair_id=x['id'], low_minutes=x['hours']['low_minutes'],
                                 high_minutes=x['hours']['high_minutes'], basis=x['hours']['basis']) for x in data['items']])
        interval = market.get('observed_range_eur')
        if interval:
            value.update(potential_gross_low_cents=interval['p25']*100-target.price_eur*100-totals['high'],
                         potential_gross_high_cents=interval['p75']*100-target.price_eur*100-totals['low'])
            value['potential_gross_low_percent'] = round(value['potential_gross_low_cents'] / (target.price_eur*100) * 100, 2)
    return value


def filter_cards(cards, **filters):
    """Unknown values never satisfy numeric filters. No fabricated zero margins."""
    allowed = set(FILTER_FIELDS) | {'min_price_eur', 'max_price_eur', 'min_mileage_km',
                                   'max_mileage_km', 'min_potential_gross_low_cents'}
    if set(filters)-allowed:
        raise ValueError('Unsupported opportunity filter')
    for key,value in filters.items():
        if key.startswith(('min_', 'max_')) and type(value) is not int:
            raise ValueError('Numeric filter requires integer')
    for field in ('price_eur','mileage_km'):
        low, high = filters.get('min_'+field),filters.get('max_'+field)
        if (low is not None and low<0) or (high is not None and high<0):
            raise ValueError('Price and mileage bounds cannot be negative')
        if low is not None and high is not None and low>high:
            raise ValueError('Filter lower bound exceeds upper bound')
    selected = []
    for item in cards:
        if item is None:
            continue
        matches = True
        for key, value in filters.items():
            if key.startswith(('min_', 'max_')):
                field = key[4:]
                actual = item.get(field)
                if type(value) is not int:
                    raise ValueError('Numeric filter requires integer')
                matches &= actual is not None and (actual >= value if key.startswith('min_') else actual <= value)
            else:
                matches &= item.get(key) == value
        if matches:
            selected.append(item)
    return selected
