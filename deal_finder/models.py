from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from urllib.parse import urlparse


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

    @classmethod
    def parse(cls, row):
        data = dict(row)
        for key in ('source', 'source_id', 'make', 'model', 'generation', 'trim',
                    'fuel', 'transmission', 'province', 'seller_type', 'condition'):
            data[key] = normalize(data[key])
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
            data['vehicle_id'] = normalize(data['vehicle_id'])
        else:
            data['vehicle_id'] = None
        return cls(**data)

    @property
    def identity(self):
        return self.source, self.source_id

    def to_dict(self):
        return asdict(self)
