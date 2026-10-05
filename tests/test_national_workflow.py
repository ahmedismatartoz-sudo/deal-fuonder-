import unittest
from datetime import timedelta
from test_core import row, NOW
from test_agents import analyze, envelope, pool
from deal_finder.agents import registry
from deal_finder.models import Listing
from deal_finder.pricing import estimate
from deal_finder.queue import Queue
from deal_finder.archive import Archive
from deal_finder.publication import PublicationAgent


class NationalWorkflowTests(unittest.TestCase):
    def test_national_fallback_explains_scope_without_relaxing_vehicle_specs(self):
        candidates = [Listing.parse(row(i,province='Roma')) for i in range(8)]
        out = analyze(envelope(), candidates, NOW)
        self.assertEqual(out['market']['data']['geographic_scope'], 'national')
        self.assertEqual(out['market']['status'], 'completed')
        mixed = [Listing.parse(row(i,province='Roma',seller_type='dealer')) for i in range(8)]
        self.assertEqual(analyze(envelope(), mixed, NOW)['market']['status'], 'blocked')

    def test_price_screen_rejects_expensive_or_non_total_targets_before_inspection(self):
        for listing in (row(99,price_eur=10000),row(99,price_eur=999),
                        row(99,price_eur=50001),row(99,price_eur=5000,price_kind='installment')):
            result = analyze(envelope(listing=listing), pool(), NOW)
            self.assertFalse(result['components']['market_selection']['data']['candidate'])
            self.assertEqual(result['condition']['status'], 'blocked')
            self.assertFalse(result['components']['supervisor']['data']['approved'])

    def test_valid_candidate_still_requires_calibrated_forecasts(self):
        result = analyze(envelope(), pool(), NOW)
        self.assertTrue(result['components']['market_selection']['data']['candidate'])
        self.assertEqual(result['components']['repairs']['status'], 'completed')
        self.assertIsNone(result['components']['resale']['data']['recommended_price_eur'])
        self.assertIsNone(result['components']['opportunity']['data']['expected_days_to_sell'])
        self.assertFalse(result['components']['publication']['data']['publishable'])
        self.assertEqual(len(registry()), 7)

    def test_non_total_comparables_do_not_enter_price_distribution(self):
        items = pool() + [Listing.parse(row(20,price_eur=1000,price_kind='deposit'))]
        self.assertEqual(estimate(Listing.parse(row(99)), items, as_of=NOW)['comparable_count'], 8)

    def test_queue_fetches_other_provinces_and_superseded_corrected_target(self):
        queue = Queue(':memory:')
        try:
            queue.submit('national',[dict(listing=row(i,province='Roma')) for i in range(8)])
            queue.submit('target',[envelope()])
            target = Listing.parse(envelope()['listing'])
            self.assertEqual(len(queue.candidates(target,NOW)), 9)
            changed = row(99,model='500',province='Padova',observed_at=NOW.isoformat())
            queue.submit('corrected',[dict(listing=changed)])
            candidates = queue.candidates(target,NOW)
            self.assertIn('500', [x.model for x in candidates if x.source_id=='99'])
            self.assertEqual(analyze(envelope(), candidates, NOW)['market']['status'], 'blocked')
        finally:
            queue.close()

    def test_publication_preview_never_releases_unapproved_or_invalid_work(self):
        queue, archive = Queue(':memory:'), Archive(':memory:')
        try:
            queue.submit('preview',[envelope(), {'listing':{'source_id':['invalid']}}])
            queue.work_one(); queue.work_one()
            result = PublicationAgent().preview(queue, archive, 'preview', as_of=NOW+timedelta(seconds=10))
            self.assertEqual(result['items'], [])
            self.assertEqual(len(result['rejected']), 2)
            self.assertFalse(result['automatic_publication_enabled'])
        finally:
            queue.close(); archive.close()

    def test_optional_listing_metadata_and_legacy_snapshot_defaults(self):
        from deal_finder.storage import Store
        import json
        store = Store(':memory:')
        try:
            legacy = Listing.parse(row())
            # Simulate an existing v0.3 row without the new optional fields.
            store.db.execute('INSERT INTO snapshots VALUES (?, ?, ?, ?)',
                             (*legacy.identity, legacy.observed_at, json.dumps(row())))
            store.db.commit()
            self.assertEqual(store.import_text(json.dumps([row()]))['duplicates'], 1)
            for data in ({'country':123}, {'latitude':45}, {'longitude':False, 'latitude':0}):
                with self.assertRaises(ValueError):
                    Listing.parse(row(**data))
        finally:
            store.close()
