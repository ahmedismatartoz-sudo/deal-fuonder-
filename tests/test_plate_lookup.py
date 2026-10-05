import os
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch
from test_core import NOW
from deal_finder.plate_lookup import PlateLookup, plate_number


def response(plate):
    return dict(results=[dict(targa=plate, completed=True, data=dict(details=dict(marca='FIAT', modello='Panda')))])


class PlateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name+'/plate.db'
        self.calls = []
        def transport(token, plate):
            self.calls.append(plate)
            return response(plate)
        self.lookup = PlateLookup(self.path, transport)
        self.env = patch.dict(os.environ, DEAL_FINDER_TUTTOTARGHE_TOKEN='synthetic-test-token',
                              DEAL_FINDER_PLATE_FREE_PLAN_CONFIRMED='tuttotarghe-direct-10-per-day')
        self.env.start()
    def tearDown(self):
        self.lookup.close(); self.env.stop(); self.tmp.cleanup()
    def test_standard_plate_validation(self):
        self.assertEqual(plate_number('ab 123-cd'), 'AB123CD')
        for value in ('bad', '../', None):
            with self.assertRaises(ValueError): plate_number(value)
    def test_cache_survives_restart_and_missing_specs_stay_missing(self):
        first = self.lookup.lookup('AB123CD', as_of=NOW)
        self.assertEqual(first['normalized']['model'], 'Panda')
        self.assertIn('engine_code', first['missing_fields'])
        self.assertFalse(first['exact_part_fitment_confirmed'])
        self.lookup.close(); self.lookup = PlateLookup(self.path, lambda *_: self.fail('Must use cache'))
        self.assertTrue(self.lookup.lookup('AB123CD', as_of=NOW)['cached'])
    def test_free_quota_survives_restart(self):
        for i in range(9):
            self.lookup.lookup(f'AB{i:03d}CD', as_of=NOW+timedelta(seconds=i*10))
        self.lookup.close(); self.lookup = PlateLookup(self.path, lambda *_: self.fail('No overage allowed'))
        self.assertEqual(self.lookup.lookup('AB999CD', as_of=NOW+timedelta(seconds=100))['status'], 'free_quota_exhausted')
    def test_failure_reservation_retained_no_retry(self):
        def fail(*_): raise RuntimeError('Synthetic outage')
        self.lookup.transport = fail
        self.assertEqual(self.lookup.lookup('AB123CD', as_of=NOW)['status'], 'provider_unavailable')
        self.assertEqual(self.lookup.lookup('AB123CD', as_of=NOW+timedelta(seconds=10))['status'], 'recent_attempt_unresolved')
    def test_missing_free_confirmation_never_calls(self):
        with patch.dict(os.environ, DEAL_FINDER_PLATE_FREE_PLAN_CONFIRMED=''):
            self.assertEqual(self.lookup.lookup('AB123CD', as_of=NOW)['status'], 'configuration_required')
        self.assertEqual(self.calls, [])
    def test_wrong_plate_preserves_raw_and_never_attests_identity(self):
        self.lookup.transport = lambda *_: response('ZZ999ZZ')
        value = self.lookup.lookup('AB123CD', as_of=NOW)
        self.assertEqual(value['status'], 'needs_review')
        self.assertIn('raw_response', value)
        self.assertFalse(value['identity_attestation'])
    def test_rate_limit_counts_only_reserved_requests(self):
        self.lookup.lookup('AB123CD', as_of=NOW)
        self.assertEqual(self.lookup.lookup('AB124CD', as_of=NOW)['status'], 'rate_limited')
        self.assertEqual(len(self.calls), 1)
    def test_api_auth_and_no_credentials(self):
        from fastapi.testclient import TestClient
        from deal_finder.api import app
        with patch.dict(os.environ, DEAL_FINDER_DB=self.path, DEAL_FINDER_API_TOKEN='test-api',
                        DEAL_FINDER_TUTTOTARGHE_TOKEN='', DEAL_FINDER_MODE='test'):
            with patch.dict(os.environ) as env:
                env.pop('DEAL_FINDER_DATABASE_URL', None)
                client = TestClient(app)
                self.assertEqual(client.post('/vehicles/plate-lookup', json={'plate':'AB123CD'}).status_code, 401)
                result = client.post('/vehicles/plate-lookup', json={'plate':'AB123CD'}, headers={'Authorization':'Bearer test-api'})
                self.assertEqual(result.json()['status'], 'configuration_required')

    def test_structured_provider_value_is_not_normalized_identity(self):
        value=response('AB123CD');value['results'][0]['data']['details']['marca']={'unexpected':'object'}
        self.lookup.transport=lambda *_:value
        result=self.lookup.lookup('AB123CD',as_of=NOW)
        self.assertEqual(result['status'],'needs_review')
        self.assertIn('make',result['conflicting_fields'])
        self.assertNotIn('make',result['normalized'])
