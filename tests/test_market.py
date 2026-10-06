import os
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch
from test_archive import page, event
from test_core import row, NOW
from deal_finder.market import Market
from deal_finder.queue import Queue


def complete(i, **changes):
    data = row(i, city='Milano', **dict({'price_kind': 'total'}, **changes))
    return event(str(i), observed_at=data['observed_at'], active=data['active'] if 'active' in data else True,
                 payload=data)


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'market.db')
        self.market = Market(self.path)
        self.queue = Queue(self.path)

    def tearDown(self):
        self.queue.close()
        self.market.close()
        self.tmp.cleanup()

    def ingest(self, records, run='initial', source='export'):
        return self.market.archive.ingest(page(records, run=run, source=source), as_of=NOW)

    def scan(self, mode='initial', **kwargs):
        return self.market.scan(mode=mode, as_of=NOW, queue=self.queue, **kwargs)

    def base(self):
        self.ingest([complete(i, price_eur=10000, vehicle_id='same-unverified-id') for i in range(8)])

    def test_initial_selection_reads_the_whole_base_before_first_target(self):
        # More than 100 records; the first target must see records appearing later.
        self.ingest([complete('000', price_eur=7000)] + [complete(i) for i in range(120)])
        result = self.scan()
        self.assertEqual(result['scanned'], 121)
        self.assertEqual(result['selected'], 1)
        candidates = self.market.candidates(as_of=NOW)['items']
        self.assertEqual(candidates[0]['benchmark']['comparable_count'], 120)
        self.assertEqual(candidates[0]['benchmark']['benchmark_eur'], 10000)
        self.assertEqual(result['queued'], 1)
        self.assertEqual(self.queue.db.execute('SELECT count(*) FROM identity_attestations').fetchone()[0], 0)
        self.assertFalse(candidates[0]['publishable'])
        self.assertIsNone(candidates[0]['recommended_resale_price_eur'])

    def test_daily_new_ad_uses_previous_base_and_retries_do_not_duplicate_jobs(self):
        self.base()
        self.scan()
        self.ingest([complete('daily', price_eur=7000, observed_at=(NOW-timedelta(hours=1)).isoformat())], run='daily')
        result = self.scan('incremental')
        self.assertEqual(result['projected'], 1)
        self.assertEqual(result['selected'], 1)
        review = self.market.candidates(as_of=NOW)['items'][0]
        self.assertEqual(review['benchmark']['comparable_count'], 8)
        self.assertTrue(all(c['source_id'] != 'daily' for c in review['benchmark']['comparables']))
        second = self.scan('incremental')
        self.assertEqual(second['scanned'], 0)
        self.assertEqual(second['queued'], 0)
        self.assertEqual(self.queue.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)

    def test_changed_or_removed_comparable_invalidates_old_candidate_before_rescan(self):
        self.base()
        self.ingest([complete('cheap', price_eur=7000)], run='cheap')
        self.scan()
        self.assertEqual(self.market.candidates(as_of=NOW)['count'], 1)
        self.ingest([complete(0, active=False, observed_at=NOW.isoformat())], run='remove')
        self.assertEqual(self.market.candidates(as_of=NOW)['count'], 0)
        self.scan('incremental')
        self.assertEqual(self.market.candidates(as_of=NOW)['count'], 0)

    def test_invalid_latest_spec_supersedes_usable_old_comparable(self):
        self.base()
        self.ingest([complete('cheap', price_eur=7000)], run='cheap')
        self.scan()
        bad = complete(0, observed_at=NOW.isoformat())
        del bad['payload']['generation']
        self.ingest([bad], run='invalid-update')
        result = self.scan('incremental')
        self.assertEqual(result['unusable'], 1)
        self.assertEqual(self.market.candidates(as_of=NOW)['count'], 0)
        self.assertEqual(self.market.status()['unprojected_events'], 0)

    def test_candidate_price_change_and_explicit_removal_hide_old_reviews(self):
        self.base()
        self.ingest([complete('cheap', price_eur=7000)], run='cheap')
        self.scan()
        self.ingest([complete('cheap', price_eur=60000, observed_at=NOW.isoformat())], run='changed')
        self.assertEqual(self.market.candidates(as_of=NOW)['count'], 0)
        self.scan('incremental')
        self.assertEqual(self.market.candidates(as_of=NOW)['count'], 0)

    def test_comparables_above_purchase_budget_still_influence_benchmark(self):
        self.ingest([complete(i, price_eur=60000) for i in range(8)] + [complete('cheap', price_eur=45000)])
        self.scan()
        candidate = self.market.candidates(as_of=NOW)['items'][0]
        self.assertEqual(candidate['benchmark']['benchmark_eur'], 60000)
        self.assertEqual(candidate['listing']['price_eur'], 45000)
        self.assertEqual(self.queue.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)

    def test_sources_are_not_blindly_pooled_without_verified_vehicle_ids(self):
        self.ingest([complete(i) for i in range(4)], source='facebook_marketplace')
        self.ingest([complete(i) for i in range(4)], source='autoscout24')
        self.ingest([complete('cheap', price_eur=7000)], run='cheap', source='facebook_marketplace')
        self.scan()
        self.assertEqual(self.market.candidates(as_of=NOW)['count'], 0)

    def test_damaged_candidate_requires_inspection_and_unknown_prices_are_excluded(self):
        self.base()
        self.ingest([event('partial'), complete('rate', price_eur=1000, price_kind='installment'),
                     complete('damaged', price_eur=2000, condition='damaged')], run='unsafe')
        self.scan()
        candidates = self.market.candidates(as_of=NOW)
        self.assertEqual(candidates['count'], 1)
        self.assertEqual(candidates['items'][0]['listing']['source_id'], 'damaged')
        self.assertFalse(candidates['items'][0]['supervisor_approved'])

    def test_report_to_queue_crash_recovers_without_duplicate_or_forged_evidence(self):
        self.base()
        self.ingest([complete('cheap', price_eur=7000)], run='cheap')
        with patch.object(self.queue, 'submit', side_effect=RuntimeError('interruption')):
            with self.assertRaises(RuntimeError):
                self.scan()
        self.assertEqual(self.scan('incremental')['queued'], 1)
        self.assertEqual(self.scan('incremental')['queued'], 0)
        with patch('deal_finder.queue.now_iso', return_value=NOW.isoformat()):
            processed = self.queue.work_one()
        self.assertEqual(processed['state'], 'done')
        outputs = self.queue.result(processed['job_id'])['run']['outputs']
        self.assertFalse(outputs['components']['supervisor']['data']['approved'])
        self.assertEqual(outputs['market']['data']['comparable_count'],8)
        self.assertEqual(outputs['market']['data']['evidence'],'unverified_source_asking_prices')
        self.assertEqual(outputs['components']['market_selection']['status'],'completed')
        self.assertEqual(outputs['identity']['status'],'needs_review')
        inputs, as_of = self.queue.db.execute('SELECT inputs, as_of FROM agent_runs WHERE job_id=?',
                                             (processed['job_id'],)).fetchone()
        from deal_finder.agents import analyze
        from deal_finder.models import Listing
        from datetime import datetime
        context = self.queue.db.json_decode(inputs)
        reproduced = analyze(context['raw'], [Listing.parse(x) for x in context['candidates']],
            datetime.fromisoformat(as_of), source_asking_candidates=[Listing.parse(x) for x in context['source_asking_candidates']])
        self.assertEqual(reproduced, outputs)

    def test_superseded_archive_snapshot_cannot_pass_queued_price_check(self):
        self.base()
        self.ingest([complete('cheap',price_eur=7000)],run='cheap')
        self.scan()
        self.ingest([complete('cheap',price_eur=11000,observed_at=NOW.isoformat())],run='changed')
        result=self.queue.work_one()
        outputs=self.queue.result(result['job_id'])['run']['outputs']
        self.assertEqual(outputs['quality']['status'],'quarantined')
        self.assertFalse(outputs['components']['supervisor']['data']['approved'])

    def test_collection_between_screening_and_analysis_refreshes_comparables(self):
        self.base()
        self.ingest([complete('cheap', price_eur=7000)], run='cheap')
        self.scan()
        self.ingest([complete('new-comparable', observed_at=NOW.isoformat())], run='concurrent')
        self.assertEqual(self.market.status()['unprojected_events'], 1)
        with patch('deal_finder.queue.now_iso', return_value=NOW.isoformat()):
            processed = self.queue.work_one()
        self.assertEqual(processed['state'], 'done')
        outputs = self.queue.result(processed['job_id'])['run']['outputs']
        self.assertEqual(outputs['components']['market_selection']['status'], 'completed')
        self.assertEqual(outputs['market']['data']['comparable_count'], 9)
        self.assertEqual(self.market.status()['unprojected_events'], 0)
        self.assertFalse(outputs['components']['supervisor']['data']['approved'])

    def test_new_data_reconsiders_previously_insufficient_cohort(self):
        self.ingest([complete(i) for i in range(7)] + [complete('cheap', price_eur=7000)])
        self.scan()
        self.assertEqual(self.market.candidates(as_of=NOW)['count'], 0)
        self.ingest([complete(7, observed_at=NOW.isoformat())], run='new-comparable')
        self.scan('incremental')
        self.assertEqual(self.market.candidates(as_of=NOW)['count'], 1)
