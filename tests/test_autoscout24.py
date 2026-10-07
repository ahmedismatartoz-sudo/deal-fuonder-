"""Synthetic fixtures shaped from public HTML inspected on 2026-10-05."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from deal_finder.archive import Archive
from deal_finder.autoscout24 import (CollectionBlocked, PublicClient, collect,
    CollectionBusy, collection_guard, cycle_run_id, page_props, page_title,
    public_url, record_event, validate_config)

NOW = datetime.now(timezone.utc).isoformat()
BASE = 'https://www.autoscout24.it'


def html(props):
    return '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps({
        'props': {'pageProps': dict(marketplace={'country': {'iso': 'it'}}, **props)}}) + '</script></html>'


def item(identifier='car1'):
    return {'id': identifier, 'url': '/annunci/fiat-panda-' + identifier,
        'vehicle': {'make': 'Fiat', 'model': 'Panda', 'modelVersionInput': '1.2 Easy',
                    'fuel': 'Benzina', 'transmission': 'Manuale'},
        'location': {'countryCode': 'IT', 'city': 'Milano'},
        'seller': {'type': 'Private'}, 'images': ['https://example.com/car.webp'],
        'tracking': {'mileage': '80000', 'firstRegistration': '05-2018'},
        'price': {'priceRaw': 8000, 'priceFormatted': '€ 8.000', 'isConditionalPrice': False}}


def detail(identifier='car1'):
    value = item(identifier)
    value['vehicle'].update(mileageInKmRaw=80000, firstRegistrationDateRaw='2018-05-01',
        transmissionType='Manuale', fuelCategory={'formatted': 'Benzina'},
        rawData={'classification': {'modelGeneration': {'formatted': 'III'}, 'trimLine': {'formatted': 'Easy'}},
                 'condition': {'damage': {'isCurrentlyDamaged': False}}})
    value.update(status='Active', description='Auto curata.<br>Tagliando recente.',
        prices={'isFinalPrice': True, 'public': {'priceRaw': 8000, 'price': '€ 8.000', 'onRequestOnly': False}},
        createdTimestampWithOffset=NOW)
    return value


def config(**kwargs):
    return dict(search_urls=[BASE + '/lst/fiat/panda?pricefrom=5000&priceto=10000'], **kwargs)


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.fail_on = None

    def get(self, url, *, kind='search'):
        self.calls.append((url, kind))
        if self.fail_on and self.fail_on in url:
            raise CollectionBlocked('AutoScout24 HTTP 429')
        if kind == 'listing':
            return html({'listingDetails': detail(url.rsplit('-', 1)[-1])})
        from urllib.parse import parse_qs, urlsplit
        number = int(parse_qs(urlsplit(url).query)['page'][0])
        return html({'listings': self.pages[number-1], 'numberOfPages': len(self.pages),
                     'numberOfResults': sum(len(p) for p in self.pages),
                     'pageQuery': {k: v[0] for k,v in parse_qs(urlsplit(url).query).items()}})


class AutoScoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'archive.db')
        self.archive = Archive(self.path)

    def tearDown(self):
        self.archive.close()
        self.temp.cleanup()

    def test_actual_data_shape_maps_precise_fields_and_keeps_original(self):
        value = detail()
        event = record_event(value, url=BASE + value['url'], observed_at=NOW, detailed=True)
        payload = event['payload']
        self.assertEqual(payload['original'], value)
        self.assertEqual(payload['price_kind'], 'total')
        self.assertEqual(payload['mileage_km'], 80000)
        self.assertEqual(payload['year'], 2018)
        self.assertEqual(payload['description'], 'Auto curata.\nTagliando recente.')
        self.assertEqual(payload['generation'], 'iii')
        self.assertEqual(payload['trim'], 'easy')
        self.assertNotIn('province', payload)
        self.assertNotIn('vehicle_id', payload)

    def test_retained_production_shape_recovers_private_seller_and_engine_without_guessing(self):
        value = detail()
        value['seller']['type'] = 'PrivateSeller'
        value['vehicle'].update(rawPowerInHp=84, rawCylinderCapacity=1248)
        value['vehicle']['rawData']['classification'] = dict(modelGeneration=None, trimLine=None)
        value['prices']['isFinalPrice'] = False
        payload = record_event(value, url=BASE + value['url'], observed_at=NOW, detailed=True)['payload']
        self.assertEqual(payload['seller_type'], 'private')
        self.assertEqual(payload['power_hp'], 84)
        self.assertEqual(payload['displacement_cc'], 1248)
        self.assertNotIn('generation', payload)
        self.assertEqual(payload['trim'],'easy')
        self.assertEqual(payload['identity_dossier']['fields']['trim']['recovery_status'],'seller_text_only')
        self.assertEqual(payload['price_kind'], 'unknown')

    def test_all_images_original_html_and_search_fields_survive_collection(self):
        original = detail()
        original['images'] = [f'https://example.com/{i}.jpg' for i in range(150)]
        original['description'] = '<p>Testo originale &amp; dettagli</p><br>Ultima riga'
        original['extra_field_from_source'] = {'not_normalized': [1, 2, 3]}
        client = FakeClient([[item()]])
        search = client.get
        client.get = lambda url, kind='search': ('<h1><span>Fiat</span><span>Panda</span></h1>' + html({'listingDetails': original})) if kind == 'listing' else search(url)
        collect(config(), self.archive, run_id='full-data', mode='initial', client=client)
        record = self.archive.history('autoscout24', 'car1')[0]['payload']
        self.assertEqual(record['original'], original)
        self.assertEqual(record['original_search'], item())
        self.assertEqual(record['description_html'], original['description'])
        self.assertIn('Ultima riga', record['description'])
        self.assertEqual(record['title'], 'Fiat Panda')
        self.assertEqual(record['field_provenance']['title'], 'detail.html.h1')
        self.assertEqual(len(record['image_urls']), 150)
        self.assertEqual(self.archive.quality()['photo_links'], 150)
        self.assertEqual(self.archive.quality()['present']['generation'], 1)
        self.assertIsNone(page_title('<h1>Uno</h1><h1>Due</h1>'))

    def test_incomplete_records_remain_accessible_and_quality_uses_latest_only(self):
        value = detail()
        value['description'] = None
        value['images'] = []
        value['vehicle']['rawData']['classification']['modelGeneration'] = None
        value['vehicle']['mileageInKmRaw'] = 0
        event = record_event(value, url=BASE + value['url'], observed_at=NOW, detailed=True)
        self.assertIn('generation', event['payload']['missing_fields'])
        self.assertNotIn('mileage_km', event['payload']['missing_fields'])
        client = FakeClient([[item()]])
        search = client.get
        client.get = lambda url, kind='search': html({'listingDetails': value}) if kind == 'listing' else search(url)
        collect(config(), self.archive, run_id='missing', mode='initial', client=client)
        collect(config(), self.archive, run_id='refreshed', mode='initial', client=FakeClient([[item()]]))
        report = self.archive.quality('autoscout24')
        self.assertEqual(report['listings'], 1)
        self.assertEqual(report['missing']['description'], 0)
        self.assertEqual(report['listings_with_photos'], 1)

    def test_concurrent_collection_is_rejected_before_network_and_lock_releases(self):
        client = FakeClient([[item()]])
        with collection_guard(self.archive):
            with self.assertRaises(CollectionBusy):
                collect(config(), self.archive, run_id='busy', client=client)
        self.assertEqual(client.calls, [])
        self.assertTrue(collect(config(), self.archive, run_id='busy', client=client)['complete'])

    def test_unavailable_specs_accident_history_and_version_are_not_guessed(self):
        value = detail()
        value['vehicle']['rawData'] = {'condition': {'damage': {'accidentFree': True}}}
        value['vehicle']['hadAccident'] = False
        payload = record_event(value, url=BASE + value['url'], observed_at=NOW, detailed=True)['payload']
        self.assertNotIn('generation', payload)
        self.assertEqual(payload['trim'],'easy')
        self.assertEqual(payload['identity_dossier']['fields']['trim']['recovery_status'],'seller_text_only')
        self.assertEqual(payload['version_text'], '1.2 Easy')
        self.assertEqual(payload['condition'], 'unknown')

    def test_conditional_and_foreign_currency_prices_never_become_total(self):
        for change in ('conditional', 'nonfinal', 'currency', 'description', 'request'):
            value = detail()
            if change == 'conditional': value['price']['isConditionalPrice'] = True
            if change == 'nonfinal': value['prices']['isFinalPrice'] = False
            if change == 'currency': value['prices']['public']['price'] = '$ 8,000'
            if change == 'description': value['description'] = 'Prezzo valido solo con finanziamento obbligatorio.'
            if change == 'request': value['prices']['public']['onRequestOnly'] = True
            payload = record_event(value, url=BASE + value['url'], observed_at=NOW, detailed=True)['payload']
            self.assertEqual(payload['price_kind'], 'unknown', change)
            if change == 'currency': self.assertNotIn('price_eur', payload)
        value = detail(); value['description'] = 'Prezzo non vincolato al finanziamento.'
        self.assertEqual(record_event(value, url=BASE + value['url'], observed_at=NOW, detailed=True)['payload']['price_kind'], 'total')
        for description in ('Anticipo di € 3.000, poi rate.', 'Solo 150 €/mese.', 'Prezzo riferito alla rata mensile.'):
            value = detail(); value['description'] = description
            self.assertEqual(record_event(value, url=BASE + value['url'], observed_at=NOW, detailed=True)['payload']['price_kind'], 'unknown')

    def test_incremental_only_fetches_new_details_and_preserves_existing_history(self):
        first = FakeClient([[item()]])
        collect(config(), self.archive, run_id='base', mode='initial', client=first)
        second = FakeClient([[item(), item('new')]])
        result = collect(config(), self.archive, run_id='day2', client=second)
        self.assertEqual(result['accepted'], 1)
        self.assertEqual([kind for _, kind in second.calls], ['search', 'listing'])
        self.assertEqual(len(self.archive.history('autoscout24', 'car1')), 1)
        self.assertEqual(len(self.archive.search()['items']), 2)

    def test_checkpoint_resume_failed_page_and_completed_retry(self):
        client = FakeClient([[item('car1')], [item('car2')]])
        first = collect(config(), self.archive, run_id='base', mode='initial', max_pages=1, client=client)
        self.assertFalse(first['complete'])
        client.fail_on = 'page=2'
        with self.assertRaises(CollectionBlocked):
            collect(config(), self.archive, run_id='base', mode='initial', client=client)
        self.assertEqual(self.archive.run_status('autoscout24', 'native-base')['pages'], 1)
        client.fail_on = None
        result = collect(config(), self.archive, run_id='base', mode='initial', client=client)
        self.assertTrue(result['complete'])
        self.assertEqual(result['accepted'], 2)
        reads = len(client.calls)
        collect(config(), self.archive, run_id='base', mode='initial', client=client)
        self.assertEqual(len(client.calls), reads)

    def test_duplicates_across_pages_and_searches_do_not_refetch_details(self):
        client = FakeClient([[item('car1'), item('car1')], [item('car1'), item('car2')]])
        result = collect(config(), self.archive, run_id='base', mode='initial', client=client)
        self.assertEqual(result['accepted'], 2)
        self.assertEqual(sum(kind == 'listing' for _, kind in client.calls), 2)

    def test_malformed_records_are_quarantined_without_losing_good_records(self):
        bad = item('broken'); bad['vehicle'] = 'not-an-object'
        forged = item('foreign'); forged['url'] = 'https://attacker.example/a'
        result = collect(config(fetch_details=False), self.archive, run_id='bad', mode='initial',
            client=FakeClient([[None, bad, forged, item()]]))
        self.assertEqual(result['accepted'], 1)
        self.assertEqual(result['quarantined'], 3)

    def test_access_challenges_and_empty_or_capped_searches_do_not_complete(self):
        for response in ('<html>captcha</html>', html({'listings': [], 'numberOfResults': 100, 'numberOfPages': 5}),
                         html({'listings': [item()], 'numberOfResults': 10000, 'numberOfPages': 200})):
            client = FakeClient([[item()]])
            client.get = lambda *a, **kw: response
            with self.assertRaises(CollectionBlocked):
                collect(config(), self.archive, run_id='blocked', mode='initial', client=client)
            with self.assertRaises(KeyError): self.archive.run_status('autoscout24', 'native-blocked')
        result = collect(config(), self.archive, run_id='empty', mode='initial', client=FakeClient([[]]))
        self.assertTrue(result['complete'])
        self.assertEqual(result['accepted'], 0)

    def test_configuration_cannot_change_during_resume(self):
        client = FakeClient([[item('car1')], [item('car2')]])
        collect(config(), self.archive, run_id='base', mode='initial', client=client, max_pages=1)
        with self.assertRaises(ValueError):
            collect(config(fetch_details=False), self.archive, run_id='base', mode='initial', client=client)

    def test_ignored_filters_do_not_silently_change_the_collection_scope(self):
        client = FakeClient([[item()]])
        original = client.get
        def wrong_filters(url, *, kind='search'):
            props = page_props(original(url, kind=kind))
            props['pageQuery']['pricefrom'] = '1000'
            props.pop('marketplace')
            return html(props)
        client.get = wrong_filters
        with self.assertRaisesRegex(CollectionBlocked, 'search filters'):
            collect(config(), self.archive, run_id='wrong-scope', mode='initial', client=client)

    def test_foreign_page_and_mismatched_detail_ids_are_blocked(self):
        with self.assertRaises(CollectionBlocked): page_props(html({}).replace('"it"', '"de"'))
        client = FakeClient([[item()]])
        original = client.get
        client.get = lambda url, kind='search': html({'listingDetails': detail('other')}) if kind == 'listing' else original(url)
        with self.assertRaises(CollectionBlocked):
            collect(config(), self.archive, run_id='mismatch', mode='initial', client=client)

    def test_http_403_429_and_redirects_are_not_bypassed(self):
        client = PublicClient()
        for status in (302, 403, 429):
            with patch.object(client.opener, 'open', side_effect=HTTPError(BASE, status, 'Denied', {}, None)), patch('deal_finder.autoscout24.time.sleep'):
                with self.assertRaisesRegex(CollectionBlocked, str(status)): client._read(BASE)

    def test_robots_denial_prevents_fetching_search(self):
        client = PublicClient()
        with patch.object(client, '_read', return_value='User-agent: *\nDisallow: /lst/') as request:
            with self.assertRaises(CollectionBlocked): client.get(BASE + '/lst/fiat')
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], BASE + '/robots.txt')

    def test_robots_query_patterns_do_not_disallow_model_paths(self):
        client = PublicClient()
        rules = 'User-agent: *\nDisallow: /lst?\nDisallow: /lst/?\nDisallow: */utils/*'
        with patch.object(client, '_read', side_effect=[rules, 'allowed']) as request:
            self.assertEqual(client.get(BASE + '/lst/fiat/panda?page=1'), 'allowed')
        self.assertEqual(request.call_count, 2)

    def test_url_and_config_guards(self):
        for url in ('http://www.autoscout24.it/lst/fiat', 'https://attacker.example/lst/fiat',
                    BASE + '/lst?make=fiat', BASE + '/lst/%2e%2e/account', BASE + '/account',
                    BASE + '/lst/fiat?cy=D', BASE + '/lst/fiat?page=2'):
            with self.assertRaises(ValueError): validate_config({'search_urls': [url]})
        for update in ({'fetch_details': 'true'}, {'max_pages_per_search': 201}, {'delay_seconds': 0}, {'unexpected': 1}):
            with self.assertRaises(ValueError): validate_config(config(**update))
        with self.assertRaises(ValueError): public_url(BASE + '/lst/fiat#fragment')
        with self.assertRaises(ValueError): validate_config({'search_urls': [BASE+'/lst/fiat', BASE+'/lst/fiat']})
        self.assertIn('sort=age', validate_config(config())['search_urls'][0])
        self.assertEqual(cycle_run_id(config(), 'initial'), cycle_run_id(config(), 'initial'))

    def test_summary_price_and_archive_quality_blocks(self):
        collect(config(fetch_details=False), self.archive, run_id='summary', mode='initial', client=FakeClient([[item()]]))
        from deal_finder.market import Market
        market = Market(self.path)
        try:
            result = market.scan(mode='initial')
            self.assertEqual(result['selected'], 0)
            self.assertNotIn('generation', self.archive.history('autoscout24', 'car1')[0]['payload'])
        finally: market.close()


class AutoScoutApiTests(unittest.TestCase):
    def test_authenticated_bounded_collection_uses_server_config(self):
        from fastapi.testclient import TestClient
        from deal_finder.api import app
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            'DEAL_FINDER_DB': str(Path(temp)/'api.db'), 'DEAL_FINDER_API_TOKEN': 'test-token',
            'DEAL_FINDER_AUTOSCOUT24_CONFIG': json.dumps(config())}):
            with TestClient(app) as client, patch('deal_finder.autoscout24.PublicClient', return_value=FakeClient([[item()]])):
                request = {'run_id': 'api-base', 'mode': 'initial'}
                self.assertEqual(client.post('/collection/autoscout24', json=request).status_code, 401)
                headers = {'authorization': 'Bearer test-token'}
                response = client.post('/collection/autoscout24', json=request, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['complete'])
                self.assertEqual(client.get('/catalogue', headers=headers).json()['items'][0]['source'], 'autoscout24')
                raw = client.get('/catalogue/autoscout24/car1', headers=headers)
                self.assertEqual(raw.status_code, 200)
                self.assertIn('original', raw.json()['payload'])
                quality = client.get('/collection/quality?source=autoscout24', headers=headers).json()
                self.assertEqual(quality['listings'], 1)
                self.assertEqual(quality['photo_storage'], 'source_urls')
                self.assertEqual(client.get('/collection/quality').status_code, 401)
                self.assertEqual(client.get('/catalogue/autoscout24/missing', headers=headers).status_code, 404)
                self.assertEqual(client.post('/collection/autoscout24', json=dict(request, max_pages=6), headers=headers).status_code, 422)

    def test_provider_failure_returns_503_and_keeps_checkpoint(self):
        from fastapi.testclient import TestClient
        from deal_finder.api import app
        fake = FakeClient([[item()]]); fake.fail_on = '/lst/'
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                'DEAL_FINDER_DB': str(Path(temp)/'api.db'), 'DEAL_FINDER_AUTOSCOUT24_CONFIG': json.dumps(config())}), \
                patch('deal_finder.autoscout24.PublicClient', return_value=fake), TestClient(app) as client:
            self.assertEqual(client.post('/collection/autoscout24', json={'run_id': 'blocked'}).status_code, 503)

    def test_worker_daily_cycle_supports_native_only_and_stable_retry(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from deal_finder.worker import main
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                'DEAL_FINDER_DB': str(Path(temp)/'cycle.db'),
                'DEAL_FINDER_COLLECTION_CONFIG': json.dumps({'autoscout24': config()})}), \
                patch('sys.argv', ['worker', 'daily-cycle', '--mode', 'initial']), \
                patch('deal_finder.autoscout24.PublicClient', return_value=FakeClient([[item()]])):
            for _ in range(2):
                output = StringIO()
                with redirect_stdout(output): main()
                result = json.loads(output.getvalue())
                self.assertEqual(result['collection'][0]['accepted'], 1)
                self.assertTrue(result['collection'][0]['complete'])

    def test_worker_does_not_screen_an_incomplete_native_cycle(self):
        from deal_finder.worker import main
        fake = FakeClient([[item()], [item('new')]])
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                'DEAL_FINDER_DB': str(Path(temp)/'cycle.db'),
                'DEAL_FINDER_COLLECTION_CONFIG': json.dumps({'autoscout24': config()})}), \
                patch('sys.argv', ['worker', 'daily-cycle', '--mode', 'initial']), \
                patch('deal_finder.autoscout24.PublicClient', return_value=fake), \
                patch('deal_finder.autoscout24.collect', return_value={'complete': False}), \
                patch('deal_finder.market.Market.scan') as scan:
            with self.assertRaises(RuntimeError): main()
            scan.assert_not_called()


class CollectionGuardRecoveryTests(unittest.TestCase):
    def test_database_unlock_failure_does_not_leak_thread_mutex(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        db=Mock();db.dialect='postgres'
        db.execute.side_effect=[Mock(fetchone=lambda:[True]),RuntimeError('Synthetic disconnected DB')]
        with self.assertRaises(RuntimeError):
            with collection_guard(SimpleNamespace(db=db)): pass
        with collection_guard(SimpleNamespace(db=SimpleNamespace(dialect='sqlite'))): pass
