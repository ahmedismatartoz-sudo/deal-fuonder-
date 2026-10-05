from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from urllib.parse import urlparse
from math import isfinite


def normalize(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Required text must be nonempty')
    return ' '.join(value.strip().casefold().split())


@dataclass(frozen=True)
class Listing:
    source: str
    source_id: str
    url: str
    make: str
    model: str
    generation: str
    trim: str
    fuel: str
    transmission: str
    year: int
    mileage_km: int
    price_eur: int
    province: str
    seller_type: str
    condition: str
    observed_at: str
    vehicle_id: str | None = None
    active: bool = True
    title: str = ''
    description: str = ''
    image_urls: list[str] | None = None
    country: str = 'IT'
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    price_kind: str = 'total'

    @classmethod
    def parse(cls, row):
        if not isinstance(row, dict):
            raise ValueError('Listing must be an object')
        data = dict(row)
        for key in ('source', 'make', 'model', 'generation', 'trim',
                    'fuel', 'transmission', 'province', 'seller_type', 'condition'):
            data[key] = normalize(data[key])
            if key in ('make', 'model', 'generation', 'trim', 'fuel', 'transmission') and data[key] in ('unknown', 'n/a', 'na', 'sconosciuto', '-'):
                raise ValueError(f'{key} cannot be an unknown placeholder')
        if not isinstance(data.get('source_id'), str) or not data['source_id'].strip():
            raise ValueError('source_id must be a nonempty case-sensitive string')
        data['source_id'] = data['source_id'].strip()
        for key in ('year', 'mileage_km', 'price_eur'):
            value = data[key]
            if isinstance(value, bool) or not str(value).isdigit():
                raise ValueError(f'{key} must be a whole number')
            data[key] = int(value)
        now = datetime.now(timezone.utc)
        if not 1950 <= data['year'] <= now.year + 1:
            raise ValueError('Invalid year')
        if not 0 <= data['mileage_km'] <= 2_000_000 or not 0 < data['price_eur'] <= 10_000_000:
            raise ValueError('Invalid mileage or EUR price')
        if not isinstance(data.get('url'), str):
            raise ValueError('URL must be a string')
        parsed = urlparse(data['url'])
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('Invalid source URL')
        if data['seller_type'] not in ('private', 'dealer'):
            raise ValueError('seller_type must be private or dealer')
        if data['condition'] not in ('undamaged', 'damaged', 'unknown'):
            raise ValueError('condition must be undamaged, damaged or unknown')
        timestamp = datetime.fromisoformat(data['observed_at'])
        if timestamp.tzinfo is None or timestamp > now:
            raise ValueError('observed_at needs timezone and cannot be in the future')
        data['observed_at'] = timestamp.astimezone(timezone.utc).isoformat()
        active = data.get('active', True)
        if active in ('true', 'false'):
            active = active == 'true'
        if not isinstance(active, bool):
            raise ValueError('active must be boolean')
        data['active'] = active
        if data.get('vehicle_id'):
            if not isinstance(data['vehicle_id'], str):
                raise ValueError('vehicle_id must be a string')
            data['vehicle_id'] = data['vehicle_id'].strip()
        else:
            data['vehicle_id'] = None
        for key in ('title', 'description'):
            if not isinstance(data.get(key, ''), str):
                raise ValueError(f'{key} must be text')
        images = data.get('image_urls')
        if images is not None:
            if not isinstance(images, list) or len(images) > 1000:
                raise ValueError('image_urls must be an array of at most 1000 URLs')
            for url in images:
                parsed = urlparse(url) if isinstance(url, str) else None
                if parsed is None or parsed.scheme not in ('http', 'https') or not parsed.netloc:
                    raise ValueError('Invalid image URL')
        country = data.get('country', 'IT')
        if not isinstance(country, str) or country.upper() != 'IT':
            raise ValueError('Only Italy is supported')
        data['country'] = 'IT'
        if data.get('city') is not None:
            data['city'] = normalize(data['city'])
        lat, lon = data.get('latitude'), data.get('longitude')
        if (lat is None) != (lon is None):
            raise ValueError('Coordinates must be paired')
        if lat is not None:
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not isfinite(x) for x in (lat, lon)):
                raise ValueError('Coordinates must be finite numbers')
            if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ValueError('Coordinates out of bounds')
        if data.get('price_kind', 'total') not in ('total', 'installment', 'deposit', 'unknown'):
            raise ValueError('Invalid price_kind')
        return cls(**data)

    @property
    def identity(self):
        return self.source, self.source_id

    def to_dict(self):
        return asdict(self)
