"""Bounded async Bright Data Facebook collection into the existing archive.

Disabled until backend key, unfunded-free-account confirmation and a reviewed
provider input configuration exist. No browser or Facebook cookies required.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from .archive import canonical

SOURCE = 'facebook_marketplace'
CONTROL = 'brightdata_control'
VERSION = 'brightdata-facebook-milano-v2'
MAX_PRICE_EXCLUSIVE_EUR = 20_000
DETAIL_DATASET = 'gd_lvt9iwuh6fbcwmx1a'
FREE_CONFIRMATION = 'unfunded-no-auto-recharge'


class ProviderError(RuntimeError):
    def __init__(self, message, *, http_status=None, phase=None):
        super().__init__(message)
        self.http_status = http_status
        self.phase = phase


class SetupError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def paused_diagnostic(error):
    # Only emit fixed codes and numeric HTTP status, never exception text,
    # provider bodies, configuration values or credentials.
    codes = {
        'api_key_missing': 'BRIGHTDATA_API_KEY is missing or empty',
        'api_key_invalid_format': 'BRIGHTDATA_API_KEY contains unsupported characters or length',
        'free_confirmation_missing': 'Free account confirmation is missing',
        'free_confirmation_invalid': 'Free account confirmation does not match the required value',
        'cycle_configuration_changed': 'Saved cycle configuration differs from current configuration',
    }
    code = error.code if isinstance(error, SetupError) and error.code in codes else None
    result = dict(brightdata='paused', error_code=code or (
        'provider_request_failed' if isinstance(error, ProviderError) else 'archive_or_configuration_invalid'),
        reason=codes[code] if code else (
            'Provider request failed; inspect status and phase before recovery' if isinstance(error, ProviderError)
            else 'Archive or collection configuration failed validation'))
    if isinstance(error, ProviderError):
        if type(error.http_status) is int and 100 <= error.http_status <= 599:
            result['http_status'] = error.http_status
        if error.phase in ('trigger', 'progress', 'snapshot', 'snapshots'):
            result['phase'] = error.phase
    return result


def resource(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r'_[A-Za-z0-9]{1,100}', value):
        raise ValueError('Invalid Bright Data resource ID')
    return value


def snapshot_resource(value):
    # Both forms appear in the provider's published API examples.
    if not isinstance(value, str) or not re.fullmatch(r'(?:sd|s)_[A-Za-z0-9]{1,100}', value):
        raise ValueError('Invalid Bright Data snapshot ID')
    return value


def item_url(value):
    p = urlsplit(value) if isinstance(value, str) else None
    if (not p or p.scheme != 'https' or p.netloc not in ('www.facebook.com', 'facebook.com')
            or p.query or p.fragment or not re.fullmatch(r'/marketplace/item/[0-9]{1,30}/?', p.path)):
        raise ValueError('Canonical public Facebook item URL required')
    return 'https://www.facebook.com' + p.path.rstrip('/') + '/'


def validate_config(config):
    if not isinstance(config, dict) or set(config) != {'cycle_id', 'dataset_id', 'discover_by', 'input', 'limit', 'schema_verified'}:
        raise ValueError('Bright Data configuration requires cycle_id, dataset_id, discover_by, input, limit and schema_verified')
    if not isinstance(config['cycle_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', config['cycle_id']):
        raise ValueError('Invalid collection cycle ID')
    resource(config['dataset_id'], 'gd')
    if config['schema_verified'] is not True:
        raise ValueError('Verify the provider input schema before enabling collection')
    if type(config['limit']) is not int or not 1 <= config['limit'] <= 100:
        raise ValueError('Trial limit must be 1..100')
    inputs = config['input']
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 10 or any(not isinstance(x, dict) for x in inputs):
        raise ValueError('Configure 1..10 provider inputs')
    if len(canonical(config).encode()) > 20000:
        raise ValueError('Collection configuration too large')
    if config['discover_by'] is None:
        if config['dataset_id'] != DETAIL_DATASET:
            raise ValueError('Unsupported detail dataset')
        for row in inputs:
            if set(row) != {'url'}:
                raise ValueError('Detail input requires only url')
            item_url(row['url'])
    elif config['discover_by'] not in ('url', 'keyword'):
        raise ValueError('Discovery must use a verified URL or keyword schema')
    return config


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, key=None):
        self.key = key if key is not None else os.getenv('BRIGHTDATA_API_KEY')
        if isinstance(self.key, str):
            self.key = self.key.strip()
        if not self.key:
            raise SetupError('api_key_missing')
        if not isinstance(self.key, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{10,1024}', self.key):
            raise SetupError('api_key_invalid_format')
        self.opener = build_opener(NoRedirect())

    def request(self, path, *, query=None, body=None):
        # Paths are constructed only by the three methods below, never from URLs.
        if not re.fullmatch(r'/datasets/v3/(trigger|snapshots|progress/(?:sd|s)_[A-Za-z0-9]+|snapshot/(?:sd|s)_[A-Za-z0-9]+)', path):
            raise ValueError('Unsupported Bright Data API path')
        phase = path.split('/')[3]
        url = 'https://api.brightdata.com' + path
        if query:
            url += '?' + urlencode(query)
        request = Request(url, data=canonical(body).encode() if body is not None else None,
                          headers={'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json',
                                   'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=30) as response:
                if response.status != 200:
                    raise ProviderError('Unexpected Bright Data response; no automatic retry', http_status=response.status, phase=phase)
                raw = response.read(20_000_001)
                if len(raw) > 20_000_000:
                    raise ProviderError('Bright Data response exceeds 20 MB', phase=phase)
                return json.loads(raw)
        except HTTPError as error:
            # Never echo bodies/headers: provider errors can contain secrets.
            raise ProviderError(f'Bright Data HTTP {error.code}; no automatic retry', http_status=error.code, phase=phase) from None
        except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError('Bright Data request failed; saved cycle retained', phase=phase) from None

    def start(self, config):
        validate_config(config)
        query = dict(dataset_id=config['dataset_id'], include_errors='true', notify='false',
                     limit_multiple_results=config['limit'])
        if config['discover_by']:
            query.update(type='discover_new', discover_by=config['discover_by'])
        result = self.request('/datasets/v3/trigger', query=query,
                              body={'input': config['input'], 'limit_per_input': config['limit']})
        if not isinstance(result, dict):
            raise ProviderError('Invalid trigger response; recover snapshot manually')
        return snapshot_resource(result.get('snapshot_id'))

    def progress(self, snapshot_id):
        result = self.request('/datasets/v3/progress/' + snapshot_resource(snapshot_id))
        if not isinstance(result, dict) or result.get('status') not in ('starting', 'running', 'ready', 'failed'):
            raise ProviderError('Unsupported snapshot progress response')
        return result['status']

    def download(self, snapshot_id):
        result = self.request('/datasets/v3/snapshot/' + snapshot_resource(snapshot_id), query={'format': 'json'})
        if not isinstance(result, list):
            raise ProviderError('Snapshot must be a JSON array')
        return result

    def snapshots(self, dataset_id, since):
        # Read-only recovery check, as used by Bright Data's official n8n
        # integration. This never triggers another collection or adopts a job.
        result = self.request('/datasets/v3/snapshots', query={
            'dataset_id': resource(dataset_id, 'gd'), 'from_date': since, 'limit': 10})
        if not isinstance(result, list):
            raise ProviderError('Unsupported snapshot list response', phase='snapshots')
        candidates = []
        for row in result:
            if not isinstance(row, dict):
                raise ProviderError('Unsupported snapshot list row', phase='snapshots')
            try:
                identifier = snapshot_resource(row.get('id'))
            except ValueError:
                raise ProviderError('Unsupported snapshot list ID', phase='snapshots') from None
            candidates.append(identifier)
        return candidates


def event(row, observed_at):
    if not isinstance(row, dict) or row.get('error') or row.get('error_code'):
        raise ValueError('Provider error or invalid row')
    if row.get('country_code') != 'IT':
        raise ValueError('Explicit Italy country code required')
    location = row.get('location')
    if not isinstance(location, str) or location.split(',')[0].strip().casefold() not in ('milano', 'milan'):
        raise ValueError('Explicit Milano city required')
    breadcrumbs = row.get('breadcrumbs', [])
    labels = [x.get('name', '') if isinstance(x, dict) else x for x in breadcrumbs] if isinstance(breadcrumbs, list) else []
    car_categories = {'cars', 'cars & trucks', 'cars and trucks', 'auto', 'automobili', 'auto e furgoni'}
    category_verified = any(isinstance(x, str) and x.strip().casefold() in car_categories for x in labels)
    vehicle_basis = 'provider_car_category'
    if not category_verified:
        # The observed provider output omits breadcrumbs even for cars. Use
        # conservative model/title + automobile-field evidence; never infer
        # a car from the discovery URL, a price or a title alone.
        title = row.get('title', '')
        description = row.get('description') or ''
        model = re.fullmatch(r'(19\d{2}|20\d{2}) (?:Fiat (?:500[XL]?|Panda|Punto|Tipo)|Audi (?:A[1-8]|Q[2-8])|BMW (?:[1-8]\d{2}[a-z]*|(?:BMW )?SERIE [1-8](?: [A-Za-z0-9]+)?))',
                             title, re.IGNORECASE) if isinstance(title, str) else None
        parts_or_other_vehicle = re.search(r'\b(?:ricambi|motore in vendita|vendo motore|smembro|monopattino|scooter|motocicletta)\b',
                                          description, re.IGNORECASE) if isinstance(description, str) else True
        miles = row.get('car_miles')
        if (not model or int(model.group(1)) > datetime.now(timezone.utc).year + 1
                or type(miles) not in (int, float) or not 0 <= miles <= 2_000_000
                or row.get('transmission') not in ('MANUAL', 'AUTOMATIC')
                or row.get('condition') not in ('USED', 'NEW') or parts_or_other_vehicle):
            raise ValueError('Car category is not verified; retain in quarantine')
        vehicle_basis = 'inferred_from_known_car_title_and_provider_vehicle_fields'
    url = item_url(row.get('url'))
    identifier = row.get('product_id')
    if type(identifier) is int:
        identifier = str(identifier)
    if identifier != url.rstrip('/').split('/')[-1]:
        raise ValueError('Listing ID and URL conflict')
    title, description = row.get('title'), row.get('description') or ''
    if not isinstance(title, str) or not title.strip() or not isinstance(description, str):
        raise ValueError('Invalid title or description')
    if row.get('currency') != 'EUR':
        raise ValueError('Explicit EUR required')
    value = row.get('final_price') if row.get('final_price') is not None else row.get('initial_price')
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 < amount <= 10_000_000 or amount != amount.to_integral_value():
            raise ValueError('Invalid EUR price')
    except InvalidOperation:
        raise ValueError('Invalid EUR price') from None
    if amount >= MAX_PRICE_EXCLUSIVE_EUR:
        raise ValueError('Collection accepts only asking amounts below 20000 EUR')
    if type(row.get('is_sold')) is not bool:
        raise ValueError('Explicit sold status required')
    if row['is_sold']:
        raise ValueError('Sold listings are excluded from this collection')
    payload = dict(country='IT', city='Milano', province='MI', title=title,
                   description=description, price_eur=int(amount), price_kind='unknown',
                   adapter=VERSION, original=row, location_basis='provider_published_country_and_city')
    payload['vehicle_type_basis'] = vehicle_basis
    images = row.get('images') or []
    if not isinstance(images, list) or len(images) > 1000:
        raise ValueError('Unsupported images')
    payload['image_urls'] = []
    for image in images:
        p = urlsplit(image) if isinstance(image, str) else None
        if not p or p.scheme != 'https' or not p.netloc:
            raise ValueError('Unsupported image URL')
        payload['image_urls'].append(image)
    for source, key in (('brand', 'make'), ('transmission', 'transmission')):
        if isinstance(row.get(source), str) and row[source].strip():
            payload[key] = row[source]
    # car_miles has no verified unit for Italian output; original evidence kept.
    # Neither year, model, trim, condition nor total cash price is guessed.
    return dict(source_id=identifier, url=url, observed_at=observed_at,
                active=not row['is_sold'], payload=payload)


def reserve(archive, run_id, scope):
    return archive.ingest(dict(source=CONTROL, run_id=run_id,
        page_id='reservation', mode='initial', scope=scope,
        input_cursor=None, next_cursor='reserved', complete=False, records=[]))


def cycle(config, archive, *, client=None, free_confirmed=False):
    config = validate_config(config)
    confirmation = os.getenv('DEAL_FINDER_BRIGHTDATA_FREE_ACCOUNT_CONFIRMED')
    if not free_confirmed and confirmation != FREE_CONFIRMATION:
        raise SetupError('free_confirmation_missing' if not confirmation else 'free_confirmation_invalid')
    # Check credentials before recording a reservation or making any request.
    client = client or Client()
    control_id = 'brightdata-' + config['cycle_id']
    scope = dict(country='IT', city='Milano', adapter=VERSION, configuration=config,
                 max_price_exclusive_eur=MAX_PRICE_EXCLUSIVE_EUR, available_only=True)
    try:
        saved = archive.run_status(CONTROL, control_id)
    except KeyError:
        saved = None
    if saved:
        if saved['scope'] != scope:
            raise SetupError('cycle_configuration_changed')
        if not saved['complete']:
            since = archive.db.execute('SELECT received_at FROM collection_pages WHERE source=? AND run_id=? AND page_id=?',
                                       (CONTROL, control_id, 'reservation')).fetchone()[0]
            if isinstance(since, datetime):
                since = since.isoformat()
            candidates = client.snapshots(config['dataset_id'], since)
            return dict(status='recovery_required', cycle_id=config['cycle_id'],
                        reason='Trigger already reserved; read-only provider check completed',
                        provider_auth_verified=True, snapshot_candidates=candidates)
    else:
        reserved = reserve(archive, control_id, scope)
        if reserved['idempotent']:
            return dict(status='recovery_required', cycle_id=config['cycle_id'])
        snapshot_id = client.start(config)
        # Store the snapshot as a second immutable page ID/cursor-free record.
        # The page ID holds the snapshot ID so immutable scope stays unchanged.
        archive.ingest(dict(source=CONTROL, run_id=control_id, page_id=snapshot_id,
            mode='initial', scope=scope, input_cursor='reserved', next_cursor=None,
            complete=True, records=[]))
    snapshot_id = archive.db.execute('SELECT page_id FROM collection_pages WHERE source=? AND run_id=? AND complete=?',
                                    (CONTROL, control_id, True)).fetchone()[0]
    snapshot_resource(snapshot_id)
    import_id = 'brightdata-' + snapshot_id
    try:
        imported = archive.run_status(SOURCE, import_id)
    except KeyError:
        imported = None
    if imported and imported['complete']:
        return dict(status='complete', snapshot_id=snapshot_id, collection=imported)
    status = client.progress(snapshot_id)
    if status != 'ready':
        return dict(status=status, snapshot_id=snapshot_id)
    rows = client.download(snapshot_id)
    if len(rows) > config['limit']:
        raise ProviderError('Provider exceeded configured record cap; import stopped')
    # Trigger reservation time is a conservative freshness bound.
    observed_at = archive.db.execute('SELECT received_at FROM collection_pages WHERE source=? AND run_id=? AND page_id=?',
                                    (CONTROL, control_id, 'reservation')).fetchone()[0]
    if isinstance(observed_at, datetime):
        observed_at = observed_at.isoformat()
    records = []
    for row in rows:
        try:
            records.append(event(row, observed_at))
        except (ValueError, TypeError, KeyError):
            records.append({'payload': {'original': row}, 'adapter_error':
                            'Unverified car, Milano location, EUR price below 20000, available listing or provider error'})
    result = archive.ingest(dict(source=SOURCE, run_id=import_id, page_id='snapshot',
        mode='initial', scope=dict(scope, snapshot_id=snapshot_id, market_coverage_verified=False),
        input_cursor=None, next_cursor=None, complete=True, records=records))
    return dict(status='complete', snapshot_id=snapshot_id, collection=result)


def revalidate_quarantine(archive, snapshot_id):
    snapshot_resource(snapshot_id)
    origin_run = 'brightdata-' + snapshot_id
    replay_run = 'brightdata-revalidate-v1-' + snapshot_id
    try:
        return archive.run_status(SOURCE, replay_run)
    except KeyError:
        pass
    origin = archive.run_status(SOURCE, origin_run)
    if not origin['complete']:
        raise ValueError('Revalidation requires a completed archived snapshot')
    # Replay only existing archived evidence, with its original observation
    # time. This consumes no provider credits and preserves the quarantine.
    observed_at = archive.db.execute('SELECT received_at FROM collection_pages WHERE source=? AND run_id=? AND page_id=?',
        (CONTROL, 'brightdata-' + origin['scope']['configuration']['cycle_id'], 'reservation')).fetchone()[0]
    if isinstance(observed_at, datetime):
        observed_at = observed_at.isoformat()
    records = []
    for (stored,) in archive.db.execute('SELECT payload FROM collection_quarantine WHERE source=? AND run_id=? ORDER BY ordinal',
                                       (SOURCE, origin_run)).fetchall():
        envelope = archive.db.json_decode(stored)
        try:
            records.append(event(envelope['payload']['original'], observed_at))
        except (ValueError, TypeError, KeyError):
            continue
    return archive.ingest(dict(source=SOURCE, run_id=replay_run, page_id='revalidated', mode='initial',
        scope=dict(country='IT', city='Milano', origin_run=origin_run, snapshot_id=snapshot_id,
                   validation_revision='vehicle-fields-v1', market_coverage_verified=False),
        input_cursor=None, next_cursor=None, complete=True, records=records))


class BackgroundCollection:
    def __init__(self, path, config):
        self.path, self.config = path, validate_config(config)
        self.last_poll = None
        self.finished = False

    def step(self):
        if self.finished or (self.last_poll is not None and time.monotonic() - self.last_poll < 60):
            return None
        self.last_poll = time.monotonic()
        from .archive import Archive
        archive = Archive(self.path)
        try:
            try:
                result = cycle(self.config, archive)
                if result['status'] == 'complete':
                    result['revalidation'] = revalidate_quarantine(archive, result['snapshot_id'])
            except (ValueError, ProviderError) as error:
                self.finished = True
                return paused_diagnostic(error)
            self.finished = result['status'] in ('complete', 'failed', 'recovery_required')
            return dict(brightdata=result['status'], **{k: v for k, v in result.items() if k != 'status'})
        finally:
            archive.close()
