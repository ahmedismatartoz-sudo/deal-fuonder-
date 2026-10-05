import unittest
from datetime import timedelta
from test_core import row, NOW
from deal_finder.archive import Archive
from deal_finder.collectors import CollectionAgent, ExportConnector


def event(i='a', **changes):
    value = dict(source_id=i, url='https://example.com/car/' + i,
                 observed_at=(NOW-timedelta(hours=2)).isoformat(), active=True,
                 payload=dict(title='Panda', description='Original full description',
                              image_urls=['https://example.com/photo.jpg'], price_eur=8000,
                              price_kind='total', city='Milano', province='Milano', country='IT',
                              latitude=45.4642, longitude=9.19))
    value.update(changes)
    return value


def page(records, run='first', **changes):
    value = dict(source='export', run_id=run, page_id='p1', mode='initial',
                 scope={'country': 'IT'}, records=records, input_cursor=None,
                 next_cursor=None, complete=True)
    value.update(changes)
    return value


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.archive = Archive(':memory:')

    def tearDown(self):
        self.archive.close()

    def ingest(self, value):
        return self.archive.ingest(value, as_of=NOW)

    def test_incomplete_originals_preserved_but_not_normalized(self):
        original = event()
        self.ingest(page([original]))
        result = self.archive.search(city='MILANO')['items']
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['payload'], original['payload'])
        with self.assertRaises(KeyError):
            self.archive.normalized_envelope('export', 'a')
        self.assertFalse(self.archive.search()['opportunities_verified'])

    def test_page_idempotency_and_changed_page_rollback(self):
        data = page([event()])
        self.assertFalse(self.ingest(data)['idempotent'])
        self.assertTrue(self.ingest(data)['idempotent'])
        changed = page([event('b')])
        with self.assertRaises(ValueError):
            self.ingest(changed)
        self.assertEqual(len(self.archive.search()['items']), 1)

    def test_invalid_or_conflicting_record_quarantined_without_overwriting(self):
        first = event()
        self.ingest(page([first]))
        conflicting = event(payload=dict(first['payload'], price_eur=7000))
        status = self.ingest(page([conflicting, {'bad': 'original'}, event('b')], run='second'))
        self.assertEqual(status['quarantined'], 2)
        self.assertEqual(status['accepted'], 1)
        self.assertEqual(self.archive.history('export', 'a')[0]['payload']['price_eur'], 8000)

    def test_incremental_removal_and_price_change_supersede_old_matches(self):
        self.ingest(page([event('a'), event('b')]))
        self.ingest(page([event('a', active=False, observed_at=NOW.isoformat()),
                          event('b', observed_at=NOW.isoformat(), payload={'price_eur':60000, 'price_kind':'total'})],
                         run='daily', mode='incremental'))
        self.assertEqual(self.archive.search()['items'], [])
        self.assertEqual(len(self.archive.history('export', 'a')), 2)

    def test_price_kinds_budget_and_unlocated_records(self):
        records = [event('valid')]
        for i, payload in enumerate(({'price_eur':500}, {'price_eur':50001},
                                      {'price_kind':'installment'}, {'price_kind':'deposit'},
                                      {'price_kind':'unknown'})):
            records.append(event(str(i), payload=dict(event()['payload'], **payload)))
        records.append(event('unknown-location', payload={'price_eur':8000, 'price_kind':'total'}))
        self.ingest(page(records))
        self.assertEqual(len(self.archive.search()['items']), 2)
        near = self.archive.search(latitude=45.4642, longitude=9.19, radius_km=5)
        self.assertEqual([v['source_id'] for v in near['items']], ['valid'])

    def test_radius_includes_nearby_other_city_and_paginates_after_distance(self):
        self.ingest(page([event('near', payload=dict(event()['payload'], city='Segrate', latitude=45.49, longitude=9.29)),
                          event('far', payload=dict(event()['payload'], city='Roma', latitude=41.9, longitude=12.49)),
                          event('a')]))
        result = self.archive.search(latitude=45.4642, longitude=9.19, radius_km=20, limit=1, offset=1)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['source_id'], 'near')
        self.assertLess(result['items'][0]['distance_km'], 20)
        self.assertEqual(len(self.archive.search(city='Milano')['items']), 1)
        with self.assertRaises(ValueError):
            self.archive.search(latitude=45.4, longitude=9.1)
        with self.assertRaises(ValueError):
            self.archive.search(min_price=999)

    def test_validation_country_future_and_coordinates(self):
        bad = [event('country', payload={'country':'FR'}),
               event('future', observed_at=(NOW+timedelta(hours=1)).isoformat()),
               event('coords', payload={'latitude':45}),
               event('image', payload={'image_urls':['file:///secret']})]
        self.assertEqual(self.ingest(page(bad))['quarantined'], 4)
        self.assertEqual(self.archive.search()['items'], [])
        with self.assertRaises(ValueError):
            self.ingest(page([], run='foreign-scope', scope={'country':'FR'}))

    def test_run_checkpoint_cursor_and_completion(self):
        self.ingest(page([event()], next_cursor='second', complete=False))
        wrong = page([event('b')], page_id='p2', input_cursor='wrong')
        with self.assertRaises(ValueError):
            self.ingest(wrong)
        correct = page([event('b')], page_id='p2', input_cursor='second')
        self.assertTrue(self.ingest(correct)['complete'])
        self.assertEqual(self.archive.run_status('export', 'first')['pages'], 2)
        with self.assertRaises(ValueError):
            self.ingest(page([], page_id='p3'))
        self.assertFalse(self.archive.run_status('export', 'first')['market_coverage_verified'])

    def test_connector_resume_from_checkpoint(self):
        connector = ExportConnector('export', [[event('a')], [event('b')]])
        agent = CollectionAgent()
        partial = agent.execute(connector, self.archive, run_id='resume', mode='initial',
                                scope={'country':'IT'}, max_pages=1)
        self.assertFalse(partial['complete'])
        complete = agent.execute(connector, self.archive, run_id='resume', mode='initial',
                                 scope={'country':'IT'}, max_pages=1)
        self.assertTrue(complete['complete'])
        self.assertEqual(complete['pages'], 2)
        self.assertEqual(len(self.archive.search()['items']), 2)

    def test_complete_listing_promotion_does_not_trust_original_attestations(self):
        payload = dict(row(99, price_eur=8000), price_kind='total',
                       identity_evidence={'verified':True}, inspection={'verified':True})
        self.ingest(page([event('99', payload=payload)]))
        promoted = self.archive.normalized_envelope('export', '99')
        self.assertEqual(set(promoted), {'listing'})
        self.assertEqual(promoted['listing']['source'], 'export')
        self.assertEqual(promoted['listing']['price_eur'], 8000)
