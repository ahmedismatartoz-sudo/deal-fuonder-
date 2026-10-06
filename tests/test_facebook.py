import json
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError
from unittest.mock import patch
from deal_finder.archive import Archive
from deal_finder.facebook import (SEARCH, SOURCE, FacebookBlocked, PublicClient,
    PublicHTML, listing_url, vehicle_event, collect_trial)

URL = 'https://www.facebook.com/marketplace/item/123/'
URL2 = 'https://www.facebook.com/marketplace/item/456/'
NOW = datetime.now(timezone.utc).isoformat()


def car(city='Milano'):
    return {'@type': 'Car', 'url': URL, 'name': 'Fiat Panda', 'description': 'Usata',
            'brand': {'name': 'Fiat'}, 'model': 'Panda',
            'address': {'addressLocality': city, 'addressCountry': 'IT'},
            'image': ['https://example.com/car.jpg'],
            'offers': {'price': '6000', 'priceCurrency': 'EUR'}}


def html(value):
    return '<script type="application/ld+json">' + json.dumps(value) + '</script>'


class FakeClient:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url):
        self.calls.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value


class FacebookTests(unittest.TestCase):
    def test_milan_evidence_and_fields(self):
        event = vehicle_event(html(car()), URL, NOW)
        self.assertEqual(event['source_id'], '123')
        self.assertEqual(event['payload']['city'], 'Milano')
        self.assertEqual(event['payload']['price_eur'], 6000)
        self.assertEqual(event['payload']['price_kind'], 'unknown')
        self.assertEqual(event['payload']['make'], 'Fiat')
        self.assertEqual(event['payload']['image_urls'], ['https://example.com/car.jpg'])
        self.assertNotIn('year', event['payload'])

    def test_outside_milan_is_excluded(self):
        self.assertIsNone(vehicle_event(html(car('Roma')), URL, NOW))
        self.assertIsNone(vehicle_event(html(car('Sesto San Giovanni')), URL, NOW))

    def test_missing_location_and_unknown_markup_stop(self):
        value = car(); del value['address']
        for text in (html(value), '<html>Accedi</html>', html({'@type': 'Product'})):
            with self.assertRaises(FacebookBlocked):
                vehicle_event(text, URL, NOW)

    def test_foreign_country_not_assumed_italian(self):
        value = car(); value['address']['addressCountry'] = 'US'
        self.assertIsNone(vehicle_event(html(value), URL, NOW))

    def test_urls_prevent_offsite_or_login_requests(self):
        for url in ('http://www.facebook.com/marketplace/item/123/',
                    'https://www.facebook.com.evil.test/marketplace/item/123/',
                    'https://www.facebook.com/login/', URL + '?token=x',
                    'https://user:pass@www.facebook.com/marketplace/item/123/'):
            with self.assertRaises(ValueError):
                listing_url(url)

    def test_links_are_canonical_and_deduplicated(self):
        links = PublicHTML('<a href="/marketplace/item/123">x</a>'
                           '<a href="/marketplace/item/123/">x</a>'
                           '<a href="https://evil.test/marketplace/item/456/">x</a>').links
        self.assertEqual(links, [URL])

    def test_checkpoint_resume_and_archive(self):
        archive = Archive(':memory:')
        client = FakeClient({SEARCH: '<a href="' + URL + '">a</a><a href="' + URL2 + '">b</a>',
                             URL: html(car()), URL2: FacebookBlocked('login')})
        try:
            with self.assertRaises(FacebookBlocked):
                collect_trial(archive, run_id='test', limit=2, client=client)
            saved = archive.run_status(SOURCE, 'facebook-trial-test')
            self.assertFalse(saved['complete']); self.assertEqual(saved['accepted'], 1)
            second = car(); second['url'] = URL2
            client.pages[URL2] = html(second)
            client.calls.clear()
            result = collect_trial(archive, run_id='test', limit=2, client=client)
            self.assertTrue(result['complete']); self.assertEqual(result['accepted'], 2)
            self.assertEqual(client.calls, [URL2])
            self.assertEqual(len(archive.history(SOURCE, '123')), 1)
            self.assertFalse(result['market_coverage_verified'])
        finally:
            archive.close()

    def test_login_does_not_create_empty_success(self):
        archive = Archive(':memory:')
        try:
            with self.assertRaises(FacebookBlocked):
                collect_trial(archive, run_id='blocked', client=FakeClient({SEARCH: '<html>login</html>'}))
            with self.assertRaises(KeyError):
                archive.run_status(SOURCE, 'facebook-trial-blocked')
        finally:
            archive.close()

    def test_robots_disallow_stops_before_search_request(self):
        client = PublicClient()
        with patch.object(client, '_read', return_value='User-agent: *\nDisallow: /') as read:
            with self.assertRaises(FacebookBlocked):
                client.get(SEARCH)
            self.assertEqual(read.call_count, 1)

    def test_http_redirect_never_followed(self):
        client = PublicClient()
        with patch.object(client.opener, 'open', side_effect=HTTPError(SEARCH, 302, 'redirect', {}, None)):
            with self.assertRaisesRegex(FacebookBlocked, 'Login or redirect'):
                client._read(SEARCH)

    def test_bounds(self):
        archive = Archive(':memory:')
        try:
            for limit in (0, 51, True, -1):
                with self.assertRaises(ValueError):
                    collect_trial(archive, run_id='a', limit=limit)
        finally:
            archive.close()
