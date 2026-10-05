import copy
import unittest
from unittest.mock import patch
from urllib.error import URLError
from datetime import timedelta
from test_core import NOW, row
from deal_finder.apify import ApifyClient, ApifyDatasetConnector, collect_tasks, mapped_event, validate_config
from deal_finder.archive import Archive
from deal_finder.collectors import CollectionAgent


MAP = {key: key for key in row() if key not in ('vehicle_id', 'observed_at', 'source')}
MAP.update(price_kind='price_kind', image_urls='photos')


class Provider:
    def __init__(self, items):
        self.items = items
        self.calls = []
        self.status = 'SUCCEEDED'

    def get(self, path, **query):
        self.calls.append((path, query))
        if path.startswith('actor-runs/'):
            run_id = path.split('/')[-1]
            return {'data': {'id': run_id, 'status': self.status, 'defaultDatasetId': 'dataset',
                             'startedAt': (NOW-timedelta(hours=2)).isoformat(),
                             'finishedAt': (NOW-timedelta(hours=1)).isoformat()}}
        if path == 'datasets/dataset':
            return {'data': {'itemCount': len(self.items)}}
        if path == 'datasets/dataset/items':
            return self.items[query['offset']:query['offset']+query['limit']]
        raise AssertionError(path)

    def runs(self, task_id):
        return [{'id': 'run1', 'status': self.status, 'finishedAt': (NOW-timedelta(hours=1)).isoformat()}]


def config(mode='incremental'):
    return {'tasks': [{'task_id': 'task1', 'source': 'facebook_marketplace', 'mode': mode,
                       'scope': {'country': 'IT'}, 'mapping': MAP, 'page_size': 2}]}


class ApifyTests(unittest.TestCase):
    def setUp(self):
        self.archive = Archive(':memory:')
        self.items = [dict(row(i), price_kind='total', photos=['https://example.com/photo.jpg']) for i in range(3)]
        self.provider = Provider(self.items)

    def tearDown(self):
        self.archive.close()

    def connector(self):
        return ApifyDatasetConnector(self.provider, source='facebook_marketplace', run_id='run1', mapping=MAP, page_size=2)

    def test_paging_resume_preserves_originals_and_observation_time(self):
        connector = self.connector()
        args = dict(run_id='apify-run1', mode='initial', scope={'country': 'IT'}, max_pages=1)
        partial = CollectionAgent().execute(connector, self.archive, **args)
        self.assertFalse(partial['complete'])
        self.assertEqual(partial['next_cursor'], '2')
        final = CollectionAgent().execute(connector, self.archive, **args)
        self.assertTrue(final['complete'])
        history = self.archive.history('facebook_marketplace', '0')[0]
        self.assertEqual(history['payload']['original'], self.items[0])
        self.assertEqual(history['observed_at'], (NOW-timedelta(hours=2)).isoformat())
        self.assertEqual(history['payload']['image_urls'], self.items[0]['photos'])
        self.assertNotIn('vehicle_id', history['payload'])

    def test_failed_or_running_runs_never_become_complete_imports(self):
        for state in ('RUNNING', 'FAILED', 'ABORTED'):
            self.provider.status = state
            with self.assertRaises(ValueError):
                self.connector()

    def test_provider_truncation_does_not_advance_archive_checkpoint(self):
        connector = self.connector()
        self.provider.items.pop()
        self.provider.items.pop()
        with self.assertRaises(RuntimeError):
            CollectionAgent().execute(connector, self.archive, run_id='broken', mode='initial', scope={'country': 'IT'})
        with self.assertRaises(KeyError):
            self.archive.run_status('facebook_marketplace', 'broken')

    def test_import_all_unseen_runs_and_retry_without_reimporting_dataset(self):
        self.provider.runs = lambda task: [
            {'id': 'run2', 'status': 'SUCCEEDED', 'finishedAt': NOW.isoformat()},
            {'id': 'run1', 'status': 'SUCCEEDED', 'finishedAt': (NOW-timedelta(hours=1)).isoformat()}]
        result = collect_tasks(config(), self.archive, client=self.provider)
        self.assertEqual([v['run_id'] for v in result], ['apify-run1', 'apify-run2'])
        dataset_reads = len(self.provider.calls)
        self.assertEqual(collect_tasks(config(), self.archive, client=self.provider), [])
        self.assertEqual(len(self.provider.calls), dataset_reads)

    def test_daily_cycle_never_runs_initial_tasks(self):
        cfg = config()
        initial = copy.deepcopy(cfg['tasks'][0])
        initial.update(task_id='initial_task', mode='initial')
        cfg['tasks'].append(initial)
        seen = []
        original = self.provider.runs
        def runs(task):
            seen.append(task)
            return original(task)
        self.provider.runs = runs
        collect_tasks(cfg, self.archive, client=self.provider)
        self.assertEqual(seen, ['task1'])

    def test_missing_price_classification_is_preserved_as_unknown(self):
        mapped = mapped_event(row(), source='facebook_marketplace', observed_at=NOW.isoformat(), mapping=MAP)
        self.archive.ingest({'source':'facebook_marketplace','run_id':'unknown','page_id':'0',
            'mode':'initial','scope':{'country':'IT'}, 'records':[mapped], 'complete':True}, as_of=NOW)
        self.assertEqual(self.archive.search()['items'], [])
        self.assertEqual(self.archive.normalized_envelope('facebook_marketplace', '0')['listing']['price_kind'], 'unknown')

    def test_incomplete_or_invalid_records_are_preserved_not_invented(self):
        self.provider.items = [{'id': 'partial', 'url':'https://example.com/partial'}, 'invalid']
        cfg = config()
        cfg['tasks'][0]['mapping'] = {'source_id':'id', 'url':'url'}
        statuses = collect_tasks(cfg, self.archive, client=self.provider)
        self.assertEqual(statuses[0]['accepted'], 1)
        self.assertEqual(statuses[0]['quarantined'], 1)
        history = self.archive.history('facebook_marketplace', 'partial')[0]
        self.assertNotIn('trim', history['payload'])
        self.assertNotIn('condition', history['payload'])
        raw = self.archive.db.execute('SELECT payload FROM collection_quarantine').fetchone()[0]
        self.assertEqual(self.archive.db.json_decode(raw)['payload']['original'], 'invalid')

    def test_changed_config_cannot_silently_reinterpret_imported_run(self):
        collect_tasks(config(), self.archive, client=self.provider)
        cfg = config()
        cfg['tasks'][0]['mapping'] = dict(MAP, model='different.path')
        with self.assertRaises(ValueError):
            collect_tasks(cfg, self.archive, client=self.provider)

    def test_explicit_value_translation_and_nested_paths(self):
        mapping = {'source_id':'id', 'url':'link', 'price_eur':'price.amount',
                   'seller_type':{'path':'seller.type','values':{'PRIVATE_SELLER':'private'}},
                   'active':{'path':'isSold','values':{'True':False,'False':True}}}
        value = mapped_event({'id':123, 'link':'https://example.com/car', 'price':{'amount':'9000'},
                              'seller':{'type':'PRIVATE_SELLER'}, 'isSold':True},
                             source='facebook_marketplace', observed_at=NOW.isoformat(), mapping=mapping)
        self.assertEqual(value['source_id'], '123')
        self.assertEqual(value['payload']['price_eur'], 9000)
        self.assertEqual(value['payload']['seller_type'], 'private')
        self.assertFalse(value['active'])

    def test_resource_urls_forged_proofs_and_duplicate_tasks_are_rejected(self):
        cfg = config()
        cfg['tasks'][0]['task_id'] = 'https://attacker.example'
        with self.assertRaises(ValueError):
            validate_config(cfg)
        cfg = config()
        cfg['tasks'].append(copy.deepcopy(cfg['tasks'][0]))
        with self.assertRaises(ValueError):
            validate_config(cfg)
        cfg = config()
        cfg['tasks'][0]['mapping']['vehicle_id'] = 'vin'
        with self.assertRaises(ValueError):
            validate_config(cfg)

    def test_http_token_stays_in_header_and_provider_errors_are_redacted(self):
        client = ApifyClient('private-test-token')
        with patch.object(client.opener, 'open', side_effect=URLError('private-test-token')) as request:
            with self.assertRaisesRegex(RuntimeError, '^Apify request failed') as raised:
                client.get('datasets/dataset', limit=10)
        req = request.call_args.args[0]
        self.assertEqual(req.get_header('Authorization'), 'Bearer private-test-token')
        self.assertNotIn('private-test-token', req.full_url)
        self.assertNotIn('private-test-token', str(raised.exception))

    def test_task_run_listing_pages_until_exhausted(self):
        client = ApifyClient('test')
        first = [{'id': 'r'+str(i)} for i in range(100)]
        with patch.object(client, 'get', side_effect=[{'data':{'items':first, 'total':101}},
                                                     {'data':{'items':[{'id':'last'}], 'total':101}}]) as get:
            runs = list(client.runs('task'))
        self.assertEqual(len(runs), 101)
        self.assertEqual(get.call_args_list[1].kwargs['offset'], 100)

    def test_currency_and_mileage_unit_guards_never_relabel_foreign_values(self):
        mapping={'source_id':'id','url':'url',
                 'price_eur':{'path':'price.amount','require':{'path':'price.currency','value':'EUR'}},
                 'mileage_km':{'path':'odometer.value','require':{'path':'odometer.unit','value':'KM'}}}
        item={'id':'1','url':'https://example.com/car','price':{'amount':'10000.00','currency':'USD'},
              'odometer':{'value':50000,'unit':'MILES'}}
        first=mapped_event(item,source='export',observed_at=NOW.isoformat(),mapping=mapping)
        self.assertNotIn('price_eur',first['payload'])
        self.assertNotIn('mileage_km',first['payload'])
        item['price']['currency']='EUR'
        item['odometer']['unit']='KM'
        result=mapped_event(item,source='export',observed_at=NOW.isoformat(),mapping=mapping)
        self.assertEqual(result['payload']['price_eur'],10000)
        self.assertEqual(result['payload']['mileage_km'],50000)
