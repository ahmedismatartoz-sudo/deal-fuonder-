"""Native collector for public Italian AutoScout24 HTML. No paid Actor required.

Parse the data actually embedded in public pages, never undocumented APIs.
Do not bypass access denials. Search exhaustion is not proof of market coverage.
"""
import json
import os
import re
import time
import hashlib
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from protego import Protego

from .archive import canonical
from .collectors import CollectionAgent, Page

ORIGIN = 'https://www.autoscout24.it'
VERSION = 'autoscout24-public-html-v4'
USER_AGENT = 'DealFinder/0.3 (+public vehicle market research)'
_collection_lock = threading.Lock()


class CollectionBlocked(RuntimeError):
    """Blocked/unsupported response: leave the collection checkpoint untouched."""


class CollectionBusy(CollectionBlocked):
    """Another process is collecting; retry later without contacting the source."""


@contextmanager
def collection_guard(archive):
    if not _collection_lock.acquire(blocking=False):
        raise CollectionBusy('AutoScout24 collector already running')
    acquired = False
    key = 'deal-finder:autoscout24-public'
    try:
        if archive.db.dialect == 'postgres':
            acquired = archive.db.execute(
                'SELECT pg_try_advisory_lock(hashtextextended(?, 0))', (key,)).fetchone()[0]
            if not acquired:
                raise CollectionBusy('AutoScout24 collector already running')
        yield
    finally:
        try:
            if acquired:
                archive.db.execute('SELECT pg_advisory_unlock(hashtextextended(?, 0))', (key,))
        finally:
            _collection_lock.release()


def public_url(value, *, kind='search'):
    if not isinstance(value, str) or not value or len(value) > 3000:
        raise ValueError('AutoScout24 requires a public Italian HTTPS URL')
    url = urljoin(ORIGIN, value)
    p = urlsplit(url)
    if (p.scheme != 'https' or p.netloc != 'www.autoscout24.it' or p.fragment
            or '%' in p.path or '\\' in value or any(ord(c) < 32 for c in value)
            or any(part in ('.', '..') for part in p.path.split('/'))):
        raise ValueError('Only www.autoscout24.it public HTTPS URLs are allowed')
    prefix = '/lst/' if kind == 'search' else '/annunci/'
    if not p.path.startswith(prefix) or not p.path[len(prefix):].strip('/'):
        raise ValueError('Use /lst/make[/model] searches or /annunci/ listing links')
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PublicClient:
    """Single-threaded, bounded requests with a per-client rate limit."""
    def __init__(self, *, delay_seconds=2):
        if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, (int, float)) or not 1 <= delay_seconds <= 60:
            raise ValueError('delay_seconds must be 1..60')
        self.delay = delay_seconds
        self.last_request = None
        self.opener = build_opener(NoRedirect())
        self.robots = None

    def _read(self, url):
        if self.last_request is not None:
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        try:
            with self.opener.open(Request(url, headers={
                    'User-Agent': USER_AGENT, 'Accept': 'text/html,text/plain',
                    'Accept-Language': 'it-IT,it;q=0.9'}), timeout=30) as response:
                if response.status != 200:
                    raise CollectionBlocked('Unexpected AutoScout24 HTTP status')
                body = response.read(8_000_001)
                if len(body) > 8_000_000:
                    raise CollectionBlocked('AutoScout24 page exceeds 8 MB')
                return body.decode('utf-8')
        except HTTPError as error:
            raise CollectionBlocked(f'AutoScout24 HTTP {error.code}; checkpoint retained, no bypass attempted') from None
        except (URLError, TimeoutError, UnicodeDecodeError):
            raise CollectionBlocked('AutoScout24 request failed; retry the same run') from None

    def get(self, url, *, kind='search'):
        url = public_url(url, kind=kind)
        if self.robots is None:
            rules = self._read(ORIGIN + '/robots.txt')
            if 'user-agent:' not in rules.casefold():
                raise CollectionBlocked('Could not verify AutoScout24 robots rules')
            self.robots = Protego.parse(rules)
            crawl_delay = self.robots.crawl_delay(USER_AGENT) or 0
            if crawl_delay > 60:
                raise CollectionBlocked('AutoScout24 requires a crawl delay beyond this collector configuration')
            self.delay = max(self.delay, crawl_delay)
        if not self.robots.can_fetch(url, USER_AGENT):
            raise CollectionBlocked('AutoScout24 robots rules disallow this URL')
        return self._read(url)


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.text = []
        self.next_data = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.current = dict(attrs)
            self.text = []

    def handle_data(self, data):
        if self.current is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            if self.current.get('id') == '__NEXT_DATA__':
                self.next_data.append(''.join(self.text))
            self.current = None


def page_props(html):
    parser = Scripts()
    parser.feed(html)
    try:
        if len(parser.next_data) != 1:
            raise ValueError()
        props = json.loads(parser.next_data[0])['props']['pageProps']
        if not isinstance(props, dict):
            raise ValueError()
        # Site/region checks prevent silently importing cross-border results.
        country = props.get('marketplace', {}).get('country', {})
        if not isinstance(country, dict) or country.get('iso') != 'it':
            raise ValueError()
        return props
    except (ValueError, KeyError, TypeError, AttributeError):
        raise CollectionBlocked('AutoScout24 schema/region changed or access challenge; checkpoint retained') from None


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ('br', 'p', 'li'):
            self.parts.append('\n')


class Headings(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.parts = []
        self.titles = []

    def handle_starttag(self, tag, attrs):
        if tag == 'h1':
            self.inside, self.parts = True, []

    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'h1' and self.inside:
            self.titles.append(' '.join(' '.join(self.parts).split()))
            self.inside = False


def page_title(html):
    headings = Headings()
    headings.feed(html)
    return headings.titles[0] if len(headings.titles) == 1 else None


def plain_text(value):
    if not isinstance(value, str):
        return ''
    parser = Text()
    parser.feed(value)
    return ''.join(parser.parts).strip()


def whole_number(value, maximum):
    if isinstance(value, bool) or value is None or not isinstance(value, (str, int, float)):
        return None
    try:
        number = Decimal(str(value))
        if number.is_finite() and 0 <= number <= maximum and number == number.to_integral_value():
            return int(number)
    except InvalidOperation:
        pass
    return None


def record_event(item, *, url, observed_at, search_url=None, detailed=False,
                 original_search=None, original_title=None):
    """Keep originals and provenance. Missing/ambiguous specifications stay missing."""
    if not isinstance(item, dict):
        return {'payload': {'original': item}}
    source_id = item.get('id')
    vehicle = item.get('vehicle') or {}
    location = item.get('location') or {}
    seller = item.get('seller') or {}
    price = item.get('price') or {}
    public = (item.get('prices') or {}).get('public') or {}
    payload = {'original': item, 'adapter': VERSION, 'country': location.get('countryCode'),
               'price_kind': 'unknown', 'condition': 'unknown',
               'field_provenance': {}, 'detail_fetched': detailed}
    if search_url:
        payload['search_url'] = search_url
    if original_search is not None:
        payload['original_search'] = original_search
    def put(key, value, path):
        if value is not None and value != '':
            payload[key] = value
            payload['field_provenance'][key] = path
    put('make', vehicle.get('make'), 'vehicle.make')
    put('model', vehicle.get('model'), 'vehicle.model')
    put('version_text', vehicle.get('modelVersionInput'), 'vehicle.modelVersionInput')
    raw = vehicle.get('rawData') or {}
    # Every published detail remains in original; expose these named sections
    # as source declarations rather than making the downstream agent guess.
    payload['declared_specs'] = {key:raw[key] for key in (
        'classification','engine','condition','maintenance','environment',
        'bodyType','numberOfDoors','interior') if raw.get(key) is not None}
    for key, field, maximum in (('power_hp', 'rawPowerInHp', 3000),
                                ('displacement_cc', 'rawCylinderCapacity', 20000)):
        put(key, whole_number(vehicle.get(field), maximum), 'vehicle.' + field)
    classification = raw.get('classification') or {}
    for key, field in (('generation', 'modelGeneration'), ('trim', 'trimLine')):
        spec = classification.get(field) or {}
        if isinstance(spec, dict):
            put(key, spec.get('formatted'), 'vehicle.rawData.classification.' + field + '.formatted')
    put('fuel', (vehicle.get('fuelCategory') or {}).get('formatted') if detailed else vehicle.get('fuel'),
        'vehicle.fuelCategory.formatted' if detailed else 'vehicle.fuel')
    put('transmission', vehicle.get('transmissionType') if detailed else vehicle.get('transmission'),
        'vehicle.transmissionType' if detailed else 'vehicle.transmission')
    if detailed:
        mileage = whole_number(vehicle.get('mileageInKmRaw'), 2_000_000)
        registered = vehicle.get('firstRegistrationDateRaw')
    else:
        mileage = whole_number((item.get('tracking') or {}).get('mileage'), 2_000_000)
        registered = (item.get('tracking') or {}).get('firstRegistration')
    put('mileage_km', mileage, 'vehicle.mileageInKmRaw' if detailed else 'tracking.mileage')
    if isinstance(registered, str):
        match = re.fullmatch(r'(\d{4})-\d{2}-\d{2}', registered) if detailed else re.fullmatch(r'\d{2}-(\d{4})', registered)
        if match and 1950 <= int(match[1]) <= datetime.now(timezone.utc).year + 1:
            put('year', int(match[1]), 'vehicle.firstRegistrationDateRaw' if detailed else 'tracking.firstRegistration')
    amount = whole_number(public.get('priceRaw') if detailed else price.get('priceRaw'), 10_000_000)
    formatted = public.get('price') if detailed else price.get('priceFormatted')
    text = plain_text(item.get('description'))
    finance_condition = re.search(
        r'(?:prezzo.{0,60}\b(?:con|tramite|(?<!non )vincolato|(?<!non )condizionato)\b.{0,25}finanziamento|'
        r'finanziamento\s+obbligatorio|solo\s+(?:con|tramite)\s+finanziamento)', text, re.I)
    price_context = '\n'.join([text, str(vehicle.get('subtitle') or ''), str(vehicle.get('modelVersionInput') or '')])
    ambiguous_payment = re.search(
        r'\b(?:rata|rate|anticipo|acconto)\s*(?:di|da|:|€)\s*[\d€]|'
        r'\b\d[\d.,]*\s*€\s*(?:/|al|a)\s*mese|'
        r'\bprezzo.{0,40}\b(?:anticipo|acconto|rata)\b', price_context, re.I)
    if amount and isinstance(formatted, str) and '€' in formatted:
        put('price_eur', amount, 'prices.public.priceRaw' if detailed else 'price.priceRaw')
        # A conditional/unclear price is archived, but cannot enter benchmarks.
        if not finance_condition and not ambiguous_payment and price.get('isConditionalPrice') is False and (not detailed or
                (item.get('prices', {}).get('isFinalPrice') is True and public.get('onRequestOnly') is False)):
            put('price_kind', 'total', 'price.isConditionalPrice=false; public EUR price')
    seller_type = {'Dealer': 'dealer', 'Private': 'private',
                   'PrivateSeller': 'private'}.get(seller.get('type'))
    put('seller_type', seller_type, 'seller.type')
    put('city', location.get('city'), 'location.city')
    # No guessed province from a free-text city or postal code.
    if location.get('latitude') is not None and location.get('longitude') is not None:
        put('latitude', location['latitude'], 'location.latitude')
        put('longitude', location['longitude'], 'location.longitude')
    damage = (raw.get('condition') or {}).get('damage') or {}
    if damage.get('isCurrentlyDamaged') is True:
        put('condition', 'damaged', 'vehicle.rawData.condition.damage.isCurrentlyDamaged')
    elif damage.get('isCurrentlyDamaged') is False:
        put('condition', 'undamaged', 'vehicle.rawData.condition.damage.isCurrentlyDamaged')
    # accidentFree / hadAccident are history, not current mechanical condition.
    title = ' '.join(str(vehicle[k]) for k in ('make', 'model', 'modelVersionInput') if vehicle.get(k))
    put('title', original_title or title,
        'detail.html.h1' if original_title else 'vehicle.make/model/modelVersionInput')
    put('description', text, 'description')
    put('description_html', item.get('description'), 'description')
    put('image_urls', item.get('images', []), 'images')
    put('created_at', item.get('createdTimestampWithOffset'), 'createdTimestampWithOffset')
    payload['missing_fields'] = [key for key in (
        'title', 'description', 'image_urls', 'make', 'model', 'version_text',
        'generation', 'trim', 'fuel', 'transmission', 'year', 'mileage_km',
        'price_eur', 'province', 'seller_type') if payload.get(key) in (None, '', [])]
    payload['source_completeness'] = dict(detail_fetched=detailed,
        published_specs_preserved=True,
        absent_from_source=[key for key in ('generation','trim','power_hp','displacement_cc')
                            if payload.get(key) is None],
        unknown_fields_are_not_zero=True)
    if detailed and item.get('status') != 'Active':
        raise CollectionBlocked('Unsupported listing availability; cannot assume it is active or sold')
    from .vehicle_identity import enrich
    payload=enrich(payload,source_url=url)
    payload['missing_fields']=[key for key in payload['missing_fields'] if payload.get(key) is None or payload.get(key) in ('',[])]
    payload['source_completeness']['absent_from_source']=payload['identity_dossier']['missing_fields']
    return {'source_id': source_id, 'url': url, 'active': True,
            'observed_at': observed_at, 'payload': payload}


def validate_config(config):
    allowed = {'search_urls', 'fetch_details', 'delay_seconds', 'max_pages_per_search'}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError('Invalid AutoScout24 configuration')
    searches = config.get('search_urls')
    if not isinstance(searches, list) or not 1 <= len(searches) <= 2000:
        raise ValueError('Configure 1..2000 AutoScout24 search URLs')
    normalized = []
    for url in searches:
        url = public_url(url)
        p = urlsplit(url)
        pairs = parse_qsl(p.query, keep_blank_values=True)
        query = dict(pairs)
        if len(query) != len(pairs) or 'page' in query or query.get('cy', 'I') != 'I':
            raise ValueError('Searches must target Italy and must not contain a page or duplicate filters')
        if query.get('atype', 'C') != 'C' or query.get('ustate', 'U') != 'U':
            raise ValueError('This collector supports used cars only')
        query.update(cy='I', atype='C', ustate='U', sort='age', desc='1')
        normalized.append(urlunsplit((p.scheme, p.netloc, p.path, urlencode(sorted(query.items())), '')))
    if len(set(normalized)) != len(normalized):
        raise ValueError('Duplicate AutoScout24 search URLs')
    details = config.get('fetch_details', True)
    if type(details) is not bool:
        raise ValueError('fetch_details must be boolean')
    limit = config.get('max_pages_per_search', 200)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('max_pages_per_search must be 1..200')
    delay = config.get('delay_seconds', 2)
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 1 <= delay <= 60:
        raise ValueError('delay_seconds must be 1..60')
    return dict(search_urls=normalized, fetch_details=details,
                max_pages_per_search=limit, delay_seconds=delay)


class AutoScout24Connector:
    source = 'autoscout24'

    def __init__(self, config, archive, *, client=None, run_id=None):
        self.config = validate_config(config)
        self.archive = archive
        self.client = client or PublicClient(delay_seconds=self.config['delay_seconds'])
        self.run_id = run_id
        from .collection_price_agent import CollectionPriceAgent, enabled
        self.price_agent = CollectionPriceAgent(archive.db) if enabled() else None

    def fetch(self, *, cursor, mode, scope):
        position = json.loads(cursor) if cursor else {'search': 0, 'page': 1}
        index, page_number = position['search'], position['page']
        if type(index) is not int or type(page_number) is not int or not 0 <= index < len(self.config['search_urls']) or not 1 <= page_number <= 200:
            raise ValueError('Invalid AutoScout24 cursor')
        search = self.config['search_urls'][index]
        url = search + '&' + urlencode({'page': page_number})
        props = page_props(self.client.get(url))
        items, total, pages = props.get('listings'), props.get('numberOfResults'), props.get('numberOfPages')
        if not isinstance(items, list) or len(items) > 100 or type(total) is not int or type(pages) is not int or total < 0 or not 0 <= pages <= 200:
            raise CollectionBlocked('AutoScout24 search schema changed')
        if total and (not items or not pages):
            raise CollectionBlocked('AutoScout24 search returned a truncated/empty page')
        if pages > self.config['max_pages_per_search'] or total > pages * 20:
            raise CollectionBlocked('Search exceeds accessible pagination; split it by model/year/price before collecting')
        expected = dict(parse_qsl(urlsplit(url).query))
        applied = props.get('pageQuery')
        if not isinstance(applied, dict) or any(str(applied.get(key)) != value for key, value in expected.items()):
            raise CollectionBlocked('AutoScout24 did not apply all requested search filters; checkpoint retained')
        records, seen = [], set()
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
                records.append({'payload': {'original': item}})
                continue
            if item['id'] in seen:
                continue
            seen.add(item['id'])
            try:
                listing_url = public_url(item.get('url'), kind='listing')
            except ValueError:
                records.append({'payload': {'original': item, 'adapter_issue': 'Invalid public listing URL'}})
                continue
            # Named scalars avoid transferring the large retained detail HTML/JSON.
            fields = ('native_run_id','price_eur','mileage_km','year','version_text','detail_fetched')
            expressions = ["payload->>'"+k+"'" if self.archive.db.dialect == 'postgres'
                           else "json_extract(payload,'$."+k+"')" for k in fields]
            known = self.archive.db.execute('SELECT observed_at,'+','.join(expressions)+
                ' FROM listing_events WHERE source=? AND source_id=? ORDER BY observed_at DESC LIMIT 1',
                (self.source,item['id'])).fetchone()
            observed = datetime.now(timezone.utc).isoformat()
            screening = mode == 'incremental' and self.price_agent is not None
            try:
                preliminary = record_event(item, url=listing_url, observed_at=observed,
                                           search_url=search, detailed=False, original_search=item)
            except (ValueError, TypeError, AttributeError):
                records.append({'payload': {'original': item, 'adapter_issue': 'Malformed listing fields'}})
                continue
            decision = None
            preliminary['payload'].update(source=self.source,source_id=item['id'],observed_at=observed)
            if known:
                if not screening and (mode == 'incremental' or known[1] == self.run_id):
                    continue
                if screening:
                    previous = tuple(str(v) if v is not None else None for v in known[2:6])
                    current = tuple(str(preliminary['payload'].get(k)) if preliminary['payload'].get(k) is not None else None
                                    for k in fields[1:5])
                    age = (datetime.now(timezone.utc)-datetime.fromisoformat(str(known[0]))).total_seconds()
                    if previous == current and age < 7*86400:
                        if known[6] in (True, 'true', '1', 1):
                            continue
                        decision = self.price_agent.screen(preliminary['payload'])
                        if not decision['detail_fetch_recommended']:
                            continue
            if screening and decision is None:
                decision = self.price_agent.screen(preliminary['payload'])
            detailed = self.config['fetch_details'] and (decision is None or decision['detail_fetch_recommended'])
            original = item
            original_title = None
            if detailed:
                detail_html = self.client.get(listing_url, kind='listing')
                detail = page_props(detail_html).get('listingDetails')
                if not isinstance(detail, dict) or detail.get('id') != item['id']:
                    raise CollectionBlocked('AutoScout24 detail ID does not match search result')
                original = detail
                original_title = page_title(detail_html)
            try:
                event = record_event(original, url=listing_url, observed_at=observed,
                                     search_url=search, detailed=detailed,
                                     original_search=item, original_title=original_title)
            except (ValueError, TypeError, AttributeError):
                records.append({'payload': {'original': original, 'adapter_issue': 'Malformed listing fields'}})
                continue
            if decision is not None:
                event['payload']['collection_price_screen'] = decision
            event['payload']['native_run_id'] = self.run_id
            records.append(event)
        # No claim that a live offset search is a frozen snapshot. Daily overlap
        # discovers missed IDs; absent results never imply removals or sales.
        if page_number < pages:
            next_position = {'search': index, 'page': page_number + 1}
        elif index + 1 < len(self.config['search_urls']):
            next_position = {'search': index + 1, 'page': 1}
        else:
            next_position = None
        return Page(records, canonical(next_position) if next_position else None, next_position is None)


def collect(config, archive, *, run_id, mode='incremental', max_pages=100, client=None):
    if mode not in ('initial', 'incremental'):
        raise ValueError('AutoScout24 mode must be initial or incremental')
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', run_id):
        raise ValueError('run_id must contain 1..120 letters, digits, underscores or hyphens')
    connector = AutoScout24Connector(config, archive, client=client, run_id=run_id)
    scope = {'country': 'IT', 'adapter': VERSION, 'configuration': connector.config,
             'purpose': 'initial_base' if mode == 'initial' else 'new_listings',
             'snapshot_consistent': False}
    if mode == 'initial':
        try:
            prior = archive.run_status('autoscout24', 'native-' + run_id)
        except KeyError:
            prior = None
        if prior and prior['complete'] and compatible_initial_scope(prior, scope):
            return prior
    if mode == 'incremental' and connector.price_agent is not None:
        from .collection_price_agent import VERSION as price_version
        scope['price_prescreen'] = price_version
    with collection_guard(archive):
        # Separate processes must also leave an interval before their first request.
        if client is None:
            time.sleep(connector.config['delay_seconds'])
        return CollectionAgent().execute(connector, archive, run_id='native-' + run_id,
                                         mode=mode, scope=scope, max_pages=max_pages)


def cycle_run_id(config, mode, cycle_id=None):
    from .collection_price_agent import enabled, VERSION as price_version
    signature_config = validate_config(config)
    if mode == 'incremental' and enabled():
        signature_config = dict(signature_config, price_prescreen=price_version)
    signature = hashlib.sha256(canonical(signature_config).encode()).hexdigest()[:16]
    period = cycle_id or ('base' if mode == 'initial' else datetime.now(timezone.utc).date().isoformat())
    return mode + '-' + period + '-' + signature


def compatible_initial_scope(status, expected):
    scope = dict(status['scope'])
    adapter = scope.pop('adapter', None)
    other = dict(expected)
    other.pop('adapter', None)
    return (status['mode'] == 'initial' and status['complete']
            and adapter in ('autoscout24-public-html-v3', VERSION) and scope == other)
