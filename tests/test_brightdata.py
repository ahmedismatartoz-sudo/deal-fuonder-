import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError
from deal_finder.archive import Archive
from deal_finder.brightdata import (Client, ProviderError, cycle, event, validate_config,
                                   BackgroundCollection, DETAIL_DATASET)

NOW = datetime.now(timezone.utc).isoformat()
URL = 'https://www.facebook.com/marketplace/item/123/'


def config():
    return dict(cycle_id='trial1', dataset_id=DETAIL_DATASET, discover_by=None,
                input=[{'url': URL}], limit=10, schema_verified=True)


def car():
    return dict(url=URL, product_id='123', title='Fiat Panda', description='Usata',
                initial_price=6000, final_price=None, currency='EUR', country_code='IT',
                location='Milano, Lombardia', breadcrumbs=['Vehicles', 'Cars & Trucks'],
                images=['https://example.com/car.jpg'], is_sold=False, car_miles=90000)


class FakeClient:
    def __init__(self):
        self.starts = 0
        self.state = 'running'
        self.rows = [car()]
        self.downloads = 0

    def start(self, config):
        self.starts += 1
        return 'sd_test123'

    def progress(self, snapshot_id):
        return self.state

    def download(self, snapshot_id):
        self.downloads += 1
        return self.rows


class BrightDataTests(unittest.TestCase):
    def test_persistence_resume_no_duplicate_job_or_ads(self):
        archive = Archive(':memory:'); client = FakeClient()
        try:
            self.assertEqual(cycle(config(), archive, client=client, free_confirmed=True)['status'], 'running')
            client.state = 'ready'
            result = cycle(config(), archive, client=client, free_confirmed=True)
            self.assertEqual(result['collection']['accepted'], 1)
            again = cycle(config(), archive, client=client, free_confirmed=True)
            self.assertEqual(again['status'], 'complete')
            self.assertEqual((client.starts, client.downloads), (1, 1))
            self.assertEqual(len(archive.history('facebook_marketplace', '123')), 1)
        finally:
            archive.close()

    def test_uncertain_post_not_retried(self):
        archive = Archive(':memory:'); client = FakeClient()
        try:
            with patch.object(client, 'start', side_effect=ProviderError('timeout')) as start:
                with self.assertRaises(ProviderError):
                    cycle(config(), archive, client=client, free_confirmed=True)
                result = cycle(config(), archive, client=client, free_confirmed=True)
                self.assertEqual(result['status'], 'recovery_required')
                self.assertEqual(start.call_count, 1)
        finally:
            archive.close()

    def test_filter_and_quarantine(self):
        archive = Archive(':memory:'); client = FakeClient(); client.state = 'ready'
        outside = car(); outside['location'] = 'Roma, Lazio'
        client.rows = [car(), outside, {'error': 'dead_page'}]
        try:
            result = cycle(config(), archive, client=client, free_confirmed=True)
            self.assertEqual(result['collection']['accepted'], 1)
            self.assertEqual(result['collection']['quarantined'], 2)
        finally:
            archive.close()

    def test_geo_currency_category_and_availability_required(self):
        for change in ({'location': 'Milano Marittima'}, {'country_code': 'US'},
                       {'currency': 'USD'}, {'breadcrumbs': ['Vehicles']},
                       {'is_sold': None}, {'product_id': '999'}):
            row = car(); row.update(change)
            with self.assertRaises(ValueError):
                event(row, NOW)

    def test_preserves_all_photos_and_ambiguous_units(self):
        row = car(); row['images'].append('https://example.com/second.jpg')
        result = event(row, NOW)
        self.assertEqual(result['payload']['image_urls'], row['images'])
        self.assertEqual(result['payload']['original']['car_miles'], 90000)
        self.assertNotIn('mileage_km', result['payload'])
        self.assertEqual(result['payload']['price_kind'], 'unknown')

    def test_total_limit_enforced(self):
        archive = Archive(':memory:'); client = FakeClient(); client.state = 'ready'; client.rows *= 11
        try:
            with self.assertRaises(ProviderError):
                cycle(config(), archive, client=client, free_confirmed=True)
            self.assertEqual(archive.history('facebook_marketplace', '123'), [])
        finally:
            archive.close()

    def test_free_confirmation_required_before_request(self):
        archive = Archive(':memory:'); client = FakeClient()
        try:
            with patch.dict('os.environ', {}, clear=True):
                with self.assertRaises(ValueError):
                    cycle(config(), archive, client=client)
            self.assertEqual(client.starts, 0)
        finally:
            archive.close()

    def test_missing_credentials_do_not_reserve(self):
        archive = Archive(':memory:')
        try:
            with patch.dict('os.environ', {}, clear=True):
                with self.assertRaises(ValueError):
                    cycle(config(), archive, free_confirmed=True)
            with self.assertRaises(KeyError):
                archive.run_status('brightdata_control', 'brightdata-trial1')
        finally:
            archive.close()

    def test_config_change_rejected(self):
        archive = Archive(':memory:'); client = FakeClient()
        try:
            cycle(config(), archive, client=client, free_confirmed=True)
            changed = config(); changed['limit'] = 20
            with self.assertRaises(ValueError):
                cycle(changed, archive, client=client, free_confirmed=True)
        finally:
            archive.close()

    def test_invalid_configuration(self):
        for change in ({'schema_verified': False}, {'limit': True}, {'limit': 101},
                       {'dataset_id': 'https://evil.test'}, {'input': [{'url': 'https://evil.test'}]}):
            value = config(); value.update(change)
            with self.assertRaises(ValueError):
                validate_config(value)

    def test_provider_http_error_redacted(self):
        client = Client(key='test-key-12345')
        with patch.object(client.opener, 'open', side_effect=HTTPError('x', 401, 'secret-key', {}, None)):
            with self.assertRaises(ProviderError) as ctx:
                client.progress('sd_test123')
            self.assertNotIn('secret-key', str(ctx.exception))

    def test_async_api_contract(self):
        client = Client(key='test-key-12345')
        with patch.object(client, 'request', return_value={'snapshot_id': 'sd_test123'}) as request:
            self.assertEqual(client.start(config()), 'sd_test123')
            args, kwargs = request.call_args
            self.assertEqual(args, ('/datasets/v3/trigger',))
            self.assertEqual(kwargs['query']['limit_multiple_results'], 10)
            self.assertEqual(kwargs['body']['limit_per_input'], 10)
        with patch.object(client, 'request', return_value={'status': 'starting'}):
            self.assertEqual(client.progress('sd_test123'), 'starting')

    def test_background_poll_throttled_and_completes(self):
        background = BackgroundCollection(':memory:', config())
        with patch('deal_finder.brightdata.cycle', return_value={'status': 'running'}) as run:
            self.assertEqual(background.step()['brightdata'], 'running')
            self.assertIsNone(background.step())
            self.assertEqual(run.call_count, 1)
