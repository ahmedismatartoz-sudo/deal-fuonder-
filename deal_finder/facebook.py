"""Bounded public-HTML Marketplace trial, not a complete Marketplace crawler.

No cookies, private APIs, browser impersonation or access-control bypass. Only
explicit structured vehicle/location evidence can enter the Milan archive.
"""
import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from protego import Protego
from .collectors import CollectionAgent, Page

ORIGIN = 'https://www.facebook.com'
SEARCH = ORIGIN + '/marketplace/milan/vehicles/'
SOURCE = 'facebook_marketplace'
VERSION = 'facebook-public-milano-trial-v1'
USER_AGENT = 'DealFinder/0.3 (+public vehicle market research)'


class FacebookBlocked(RuntimeError):
    pass


def listing_url(value):
    p = urlsplit(value) if isinstance(value, str) else None
    if (not p or p.scheme != 'https' or p.netloc != 'www.facebook.com'
            or p.query or p.fragment or not re.fullmatch(r'/marketplace/item/[0-9]{1,30}/?', p.path)):
        raise ValueError('Only public canonical Facebook Marketplace item URLs allowed')
    return ORIGIN + p.path.rstrip('/') + '/'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PublicClient:
    def __init__(self):
        self.opener = build_opener(NoRedirect())
        self.robots = None
        self.last_request = None
        self.delay = 3

    def _read(self, url):
        if self.last_request is not None:
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        try:
            with self.opener.open(Request(url, headers={'User-Agent': USER_AGENT,
                        'Accept': 'text/html,text/plain', 'Accept-Language': 'it-IT,it;q=0.9'}), timeout=30) as response:
                if response.status != 200:
                    raise FacebookBlocked('Unexpected Facebook response')
                body = response.read(8_000_001)
                if len(body) > 8_000_000:
                    raise FacebookBlocked('Facebook page exceeds 8 MB')
                return body.decode('utf-8')
        except HTTPError as error:
            reason = 'Login or redirect required' if 300 <= error.code < 400 else f'HTTP {error.code}'
            raise FacebookBlocked(reason + '; no bypass attempted') from None
        except (URLError, TimeoutError, UnicodeDecodeError):
            raise FacebookBlocked('Facebook public request failed') from None

    def get(self, url):
        if url != SEARCH:
            listing_url(url)
        if self.robots is None:
            text = self._read(ORIGIN + '/robots.txt')
            if 'user-agent:' not in text.casefold():
                raise FacebookBlocked('Could not verify Facebook robots rules')
            self.robots = Protego.parse(text)
            delay = self.robots.crawl_delay(USER_AGENT) or 0
            if delay > 60:
                raise FacebookBlocked('Required crawl delay exceeds trial limit')
            self.delay = max(self.delay, delay)
        if not self.robots.can_fetch(url, USER_AGENT):
            raise FacebookBlocked('Facebook robots rules disallow collection')
        return self._read(url)


class PublicHTML(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links, self.documents, self.parts = [], [], None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('href'):
            try:
                url = listing_url(urljoin(ORIGIN, attrs['href']))
                if url not in self.links:
                    self.links.append(url)
            except ValueError:
                pass
        if tag == 'script' and attrs.get('type', '').lower() == 'application/ld+json':
            self.parts = []

    def handle_data(self, data):
        if self.parts is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.parts is not None:
            try:
                self.documents.append(json.loads(''.join(self.parts)))
            except json.JSONDecodeError:
                pass
            self.parts = None


def nodes(doc):
    if isinstance(doc, list):
        for value in doc:
            yield from nodes(value)
    elif isinstance(doc, dict):
        yield doc
        for key in ('@graph', 'itemListElement', 'item'):
            if key in doc:
                yield from nodes(doc[key])


def vehicle_event(text, url, observed_at):
    """Return None for explicitly out-of-scope cars; unknown markup stops trial."""
    url = listing_url(url)
    for document in PublicHTML(text).documents:
        for item in nodes(document):
            types = item.get('@type', [])
            types = [types] if isinstance(types, str) else types
            if not isinstance(types, list) or not {'Car', 'Vehicle'} & set(t for t in types if isinstance(t, str)):
                continue
            if item.get('url') and listing_url(item['url']) != url:
                continue
            location = item.get('availableAtOrFrom', {})
            address = location.get('address', {}) if isinstance(location, dict) else {}
            if not address:
                address = item.get('address', {})
            if not isinstance(address, dict):
                raise FacebookBlocked('Vehicle location unavailable; nothing imported')
            city, country = address.get('addressLocality'), address.get('addressCountry')
            if isinstance(country, dict):
                country = country.get('name')
            if not isinstance(city, str) or not isinstance(country, str):
                raise FacebookBlocked('Explicit vehicle city and country required')
            if city.strip().casefold() not in ('milano', 'milan') or country.strip().casefold() not in ('it', 'italia', 'italy'):
                return None
            title, description = item.get('name'), item.get('description', '')
            if not isinstance(title, str) or not title.strip() or not isinstance(description, str):
                raise FacebookBlocked('Vehicle title/description unavailable')
            payload = dict(country='IT', city='Milano', province='MI', title=title,
                           description=description, adapter=VERSION, price_kind='unknown',
                           location_basis='published_structured_vehicle_address')
            images = item.get('image', [])
            images = [images] if isinstance(images, (str, dict)) else images
            if not isinstance(images, list) or len(images) > 1000:
                raise FacebookBlocked('Unsupported image data')
            payload['image_urls'] = []
            for image in images:
                image = image.get('url') if isinstance(image, dict) else image
                if isinstance(image, str) and urlsplit(image).scheme == 'https' and urlsplit(image).netloc:
                    payload['image_urls'].append(image)
            offer = item.get('offers', {})
            if isinstance(offer, dict) and offer.get('priceCurrency') == 'EUR':
                try:
                    price = Decimal(str(offer['price']))
                    if price.is_finite() and 0 < price <= 10_000_000 and price == price.to_integral_value():
                        payload['price_eur'] = int(price)
                except (KeyError, InvalidOperation):
                    pass
            payload['published_vehicle_data'] = {key: value for key, value in item.items()
                                                 if key not in ('seller', 'contactPoint', 'telephone', 'email')}
            brand = item.get('brand')
            if isinstance(brand, dict):
                brand = brand.get('name')
            if isinstance(brand, str) and brand.strip():
                payload['make'] = brand
            for source_key, key in (('model', 'model'), ('vehicleConfiguration', 'trim'),
                                    ('fuelType', 'fuel'), ('vehicleTransmission', 'transmission')):
                value = item.get(source_key)
                if isinstance(value, str) and value.strip():
                    payload[key] = value
            # An advertised amount alone does not prove a total cash price.
            # No guessed engine, year, condition, odometer or seller identity.
            return dict(source_id=urlsplit(url).path.split('/')[3], url=url,
                        observed_at=observed_at, active=True, payload=payload)
    raise FacebookBlocked('No supported public structured vehicle data; nothing imported')


class TrialConnector:
    source = SOURCE

    def __init__(self, client, urls):
        self.client, self.urls = client, urls

    def fetch(self, *, cursor, mode, scope):
        index = int(cursor) if cursor is not None else 0
        if not 0 <= index < len(self.urls):
            raise ValueError('Invalid trial cursor')
        url = self.urls[index]
        text = self.client.get(url)
        event = vehicle_event(text, url, datetime.now(timezone.utc).isoformat())
        end = index + 1
        return Page([event] if event else [], str(end) if end < len(self.urls) else None, end == len(self.urls))


def collect_trial(archive, *, run_id, limit=10, client=None):
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', run_id):
        raise ValueError('Invalid trial run ID')
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Trial limit must be 1..50')
    saved_id = 'facebook-trial-' + run_id
    try:
        saved = archive.run_status(SOURCE, saved_id)
    except KeyError:
        saved = None
    if saved:
        scope = saved['scope']
        if scope.get('adapter') != VERSION or scope.get('limit') != limit:
            raise ValueError('Trial configuration changed; reuse the original limit')
        if saved['complete']:
            return saved
        urls = scope['listing_urls']
    else:
        client = client or PublicClient()
        urls = PublicHTML(client.get(SEARCH)).links[:limit]
        if not urls:
            raise FacebookBlocked('No public listing links; login or unsupported page')
        scope = dict(country='IT', city='Milano', adapter=VERSION, limit=limit,
                     listing_urls=urls, coverage='bounded_sample', market_coverage_verified=False)
    client = client or PublicClient()
    return CollectionAgent().execute(TrialConnector(client, urls), archive, run_id=saved_id,
                                     mode='initial', scope=scope, max_pages=limit)
