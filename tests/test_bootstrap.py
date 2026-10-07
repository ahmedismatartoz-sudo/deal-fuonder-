import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
from deal_finder.archive import Archive
from deal_finder.bootstrap import Bootstrap, bootstrap_config
from deal_finder.autoscout24 import CollectionBlocked, CollectionBusy
from test_autoscout24 import config, FakeClient, item


class BootstrapTests(unittest.TestCase):
    def test_milano_profile_prioritizes_private_and_keeps_geographic_filters(self):
        from urllib.parse import parse_qs, urlsplit
        with patch.dict(os.environ, {'DEAL_FINDER_AUTOSCOUT24_BOOTSTRAP':
                json.dumps({'run_id': 'milano', 'profile': 'milano-100km'})}):
            searches = bootstrap_config()['config']['search_urls']
        self.assertEqual(len(searches), 1404)
        for index, url in enumerate(searches):
            filters = parse_qs(urlsplit(url).query)
            self.assertEqual(filters['zip'], ['20121'])
            self.assertEqual(filters['zipr'], ['100'])
            self.assertEqual(filters.get('custtype'), ['P'] if index < 702 else None)

    def test_resume_after_restart_and_completed_restart_makes_no_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp)/'base.db')
            spec = {'run_id': 'wide-base', 'config': config()}
            fake = FakeClient([[item('a')], [item('b')]])
            with patch('deal_finder.autoscout24.PublicClient', return_value=fake), patch('deal_finder.autoscout24.time.sleep'):
                first = Bootstrap(path, spec).step()
                self.assertEqual(first['accepted'], 1)
                self.assertFalse(first['complete'])
                restarted = Bootstrap(path, spec)
                final = restarted.step()
                self.assertTrue(final['complete'])
                self.assertEqual(final['accepted'], 2)
                self.assertEqual(final['market']['projected'], 2)
                calls = len(fake.calls)
                self.assertIsNone(restarted.step())
                self.assertTrue(Bootstrap(path, spec).step()['complete'])
                self.assertEqual(len(fake.calls), calls)

    def test_source_block_pauses_without_repeated_requests_and_busy_can_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            boot = Bootstrap(str(Path(tmp)/'base.db'), {'run_id': 'base', 'config': config()})
            with patch('deal_finder.bootstrap.collect', side_effect=CollectionBusy('occupied')) as request:
                self.assertIsNone(boot.step())
                self.assertFalse(boot.paused)
            with patch('deal_finder.bootstrap.collect', side_effect=CollectionBlocked('HTTP 403')) as request:
                self.assertEqual(boot.step()['bootstrap'], 'paused')
                self.assertIsNone(boot.step())
                self.assertEqual(request.call_count, 1)

    def test_daily_cycle_waits_for_base_and_rejects_changed_scope(self):
        from deal_finder.worker import main
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp)/'base.db')
            spec = {'run_id': 'base', 'config': config()}
            with patch.dict(os.environ, {'DEAL_FINDER_DB': path,
                    'DEAL_FINDER_AUTOSCOUT24_BOOTSTRAP': json.dumps(spec),
                    'DEAL_FINDER_AUTOSCOUT24_CONFIG': json.dumps(config())}), \
                    patch('sys.argv', ['worker', 'daily-cycle']), \
                    patch('deal_finder.autoscout24.collect') as request:
                output = StringIO()
                with redirect_stdout(output): main()
                self.assertTrue(json.loads(output.getvalue())['waiting_for_initial_base'])
                request.assert_not_called()
                validated = bootstrap_config()
                self.assertIn('sort=age', validated['config']['search_urls'][0])
            fake = FakeClient([[item()]])
            with patch('deal_finder.autoscout24.PublicClient', return_value=fake), patch('deal_finder.autoscout24.time.sleep'):
                Bootstrap(path, spec).step()
            with patch.dict(os.environ, {'DEAL_FINDER_DB': path,
                    'DEAL_FINDER_AUTOSCOUT24_BOOTSTRAP': json.dumps(spec),
                    'DEAL_FINDER_AUTOSCOUT24_CONFIG': json.dumps(config(fetch_details=False))}), \
                    patch('sys.argv', ['worker', 'daily-cycle']):
                with self.assertRaisesRegex(ValueError, 'match'):
                    main()

    def test_invalid_bootstrap_config_is_not_silently_ignored(self):
        for value in ({'run_id': 'base'}, {'run_id': '../bad', 'config': config()},
                      {'run_id': 'base', 'config': config(), 'unexpected': True}):
            with patch.dict(os.environ, {'DEAL_FINDER_AUTOSCOUT24_BOOTSTRAP': json.dumps(value)}):
                with self.assertRaises(ValueError): bootstrap_config()


class TransientBootstrapTests(unittest.TestCase):
    def test_statement_timeout_keeps_worker_alive_and_checkpoint_for_retry(self):
        class TimeoutError(Exception):sqlstate='57014'
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'base.db')
            boot=Bootstrap(path,dict(run_id='base',config=config()))
            with patch('deal_finder.bootstrap.collect',side_effect=TimeoutError()) as request:
                value=boot.step()
                self.assertEqual(value['bootstrap'],'retry_later');self.assertTrue(value['checkpoint_retained'])
                self.assertFalse(boot.paused);self.assertFalse(boot.finished)
                self.assertIsNone(boot.step());self.assertEqual(request.call_count,1)


class CollectionBatchTests(unittest.TestCase):
    @patch.dict(os.environ,{'DEAL_FINDER_AUTOSCOUT24_PAGES_PER_STEP':'5'})
    def test_larger_batch_reuses_client_and_preserves_completion_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'base.db');spec=dict(run_id='fast',config=config(fetch_details=False))
            fake=FakeClient([[item('a')],[item('b')],[item('c')]])
            with patch('deal_finder.autoscout24.PublicClient',return_value=fake) as factory,patch('deal_finder.autoscout24.time.sleep'):
                result=Bootstrap(path,spec).step()
                self.assertTrue(result['complete'])
                self.assertEqual(result['accepted'],3)
                self.assertEqual(factory.call_count,1)
                self.assertTrue(Bootstrap(path,spec).step()['complete'])
                self.assertEqual(len(fake.calls),3)
