import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError
from deal_finder.archive import Archive
from deal_finder.brightdata import (Client, ProviderError, cycle, event, validate_config,
                                   BackgroundCollection, DETAIL_DATASET, FREE_CONFIRMATION,
                                   SetupError, paused_diagnostic, revalidate_quarantine)

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

    def snapshots(self, dataset_id, since):
        return []


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
                self.assertTrue(result['provider_auth_verified'])
                self.assertEqual(result['snapshot_candidates'], [])
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

    def test_milano_price_cap_and_unsold_only(self):
        for change in ({'initial_price': 20000}, {'final_price': 23000}, {'is_sold': True}):
            row = car(); row.update(change)
            with self.assertRaises(ValueError):
                event(row, NOW)
        row = car(); row['initial_price'] = 19999
        self.assertEqual(event(row, NOW)['payload']['price_eur'], 19999)

    def test_over_budget_and_sold_rows_quarantined(self):
        archive = Archive(':memory:'); client = FakeClient(); client.state = 'ready'
        expensive = car(); expensive['initial_price'] = 20000
        sold = car(); sold['is_sold'] = True
        client.rows = [car(), expensive, sold]
        try:
            result = cycle(config(), archive, client=client, free_confirmed=True)
            self.assertEqual(result['collection']['accepted'], 1)
            self.assertEqual(result['collection']['quarantined'], 2)
        finally:
            archive.close()

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

    def test_setup_diagnostics_identify_failure_before_request(self):
        cases = [({}, 'free_confirmation_missing'),
                 ({'DEAL_FINDER_BRIGHTDATA_FREE_ACCOUNT_CONFIRMED': 'wrong'}, 'free_confirmation_invalid'),
                 ({'DEAL_FINDER_BRIGHTDATA_FREE_ACCOUNT_CONFIRMED': FREE_CONFIRMATION}, 'api_key_missing'),
                 ({'DEAL_FINDER_BRIGHTDATA_FREE_ACCOUNT_CONFIRMED': FREE_CONFIRMATION,
                   'BRIGHTDATA_API_KEY': 'secret invalid key'}, 'api_key_invalid_format')]
        for env, expected in cases:
            with self.subTest(expected=expected), patch.dict('os.environ', env, clear=True), \
                    patch('deal_finder.brightdata.build_opener') as opener:
                background = BackgroundCollection(':memory:', config())
                result = background.step()
                self.assertEqual(result['error_code'], expected)
                self.assertIsNone(background.step())
                opener.assert_not_called()
                self.assertNotIn('secret invalid key', str(result))

    def test_copied_key_surrounding_whitespace_is_removed(self):
        client = Client(key=' \ntest-key-12345\t ')
        self.assertEqual(client.key, 'test-key-12345')
        with self.assertRaises(SetupError):
            Client(key='test-key-12345\nInjected: value')

    def test_http_diagnostic_redacts_secrets_and_reports_phase(self):
        client = Client(key='test-key-12345')
        with patch.object(client.opener, 'open', side_effect=HTTPError('secret-url', 401, 'secret-message', {}, None)):
            with self.assertRaises(ProviderError) as ctx:
                client.progress('sd_test123')
        result = paused_diagnostic(ctx.exception)
        self.assertEqual(result['http_status'], 401)
        self.assertEqual(result['phase'], 'progress')
        for secret in ('test-key-12345', 'secret-url', 'secret-message'):
            self.assertNotIn(secret, str(result))

    def test_arbitrary_exception_details_never_logged(self):
        for error in (ValueError('secret-config'), ProviderError('secret-body', phase='secret-key'), SetupError('secret-code')):
            result = paused_diagnostic(error)
            self.assertNotIn('secret', str(result))

    def test_recovery_uses_only_readonly_snapshot_list(self):
        client = Client(key='test-key-12345')
        with patch.object(client, 'request', return_value=[{'id': 'sd_job123'}, {'id': 's_job456'}]) as request:
            self.assertEqual(client.snapshots(DETAIL_DATASET, NOW), ['sd_job123', 's_job456'])
            self.assertEqual(request.call_args.args, ('/datasets/v3/snapshots',))
            self.assertNotIn('body', request.call_args.kwargs)
        with patch.object(client, 'request', return_value=[{'id': 'secret-invalid'}]):
            with self.assertRaises(ProviderError):
                client.snapshots(DETAIL_DATASET, NOW)

    def test_published_short_snapshot_prefix_supported(self):
        client = Client(key='test-key-12345')
        with patch.object(client, 'request', return_value={'snapshot_id': 's_job123'}):
            self.assertEqual(client.start(config()), 's_job123')

    def test_observed_vehicle_fields_fallback_requires_all_evidence(self):
        row = car(); row.update(title='2014 Fiat 500', breadcrumbs=None, transmission='MANUAL', condition='USED')
        accepted = event(row, NOW)
        self.assertEqual(accepted['payload']['vehicle_type_basis'], 'inferred_from_known_car_title_and_provider_vehicle_fields')
        self.assertEqual(accepted['payload']['original'], row)
        self.assertNotIn('mileage_km', accepted['payload'])
        for change in ({'transmission': None}, {'car_miles': None}, {'condition': None},
                       {'title': '2020 BMW R1250'}, {'title': 'Sgomberi'},
                       {'description': 'Vendo motore Fiat 500'}, {'location': 'Bergamo, Italia'},
                       {'initial_price': 20000}):
            invalid = dict(row, **change)
            with self.assertRaises(ValueError):
                event(invalid, NOW)

    def test_revalidation_reuses_archived_data_once_without_provider(self):
        archive = Archive(':memory:'); client = FakeClient(); client.state = 'ready'
        row = car(); row.update(title='2014 Fiat 500', breadcrumbs=None, transmission='MANUAL', condition='USED')
        outside = dict(row, product_id='456', url=URL.replace('123', '456'), location='Bergamo, Italia')
        client.rows = [row, outside]
        try:
            with patch('deal_finder.brightdata.event', side_effect=ValueError('old validation')):
                cycle(config(), archive, client=client, free_confirmed=True)
            result = revalidate_quarantine(archive, 'sd_test123')
            self.assertEqual(result['accepted'], 1)
            revalidate_quarantine(archive, 'sd_test123')
            self.assertEqual(len(archive.history(SOURCE := 'facebook_marketplace', '123')), 1)
            self.assertEqual(client.starts, 1)
            self.assertEqual(archive.run_status(SOURCE, 'brightdata-sd_test123')['quarantined'], 2)
        finally:
            archive.close()
