import os
import tempfile
import unittest
from unittest.mock import patch
from deal_finder.archive import Archive
from deal_finder.brightdata import (cycle, FREE_CONFIRMATION, ProviderError,
                                   BackgroundArchiveRevalidation, LEGACY_VERSION, CONTROL)
from deal_finder.brightdata_campaign import (BackgroundCampaign, credit_ledger, config_for,
                                           search_plan, validate_spec, PLAN_VERSION)
from test_brightdata import FakeClient, car, config


def spec(ceiling=100, batch=10):
    return dict(campaign_id='test-campaign', credit_ceiling=ceiling, batch_limit=batch, plan_version=PLAN_VERSION)


class CampaignTests(unittest.TestCase):
    def test_nearby_plan_widens_radius_without_changing_legacy_reserved_inputs(self):
        from deal_finder.brightdata_campaign import NEARBY_PLAN_VERSION
        from urllib.parse import parse_qs,urlparse
        legacy=config_for(spec(),0,40)
        nearby=dict(spec(5000,40),campaign_id='nearby-new',plan_version=NEARBY_PLAN_VERSION)
        validate_spec(nearby)
        current=config_for(nearby,0,40)
        self.assertEqual(parse_qs(urlparse(legacy['input'][0]['url']).query)['radius'],['5'])
        self.assertEqual(parse_qs(urlparse(current['input'][0]['url']).query)['radius'],['100'])
        self.assertNotEqual(current['cycle_id'],legacy['cycle_id'])
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'archive.db')
        self.env = patch.dict('os.environ', {'DEAL_FINDER_BRIGHTDATA_FREE_ACCOUNT_CONFIRMED': FREE_CONFIRMATION})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_resume_after_restart_does_not_trigger_twice(self):
        client = FakeClient()
        first = BackgroundCampaign(self.path, spec(), client=client)
        self.assertEqual(first.step()['brightdata'], 'campaign_running')
        resumed = BackgroundCampaign(self.path, spec(), client=client)
        self.assertEqual(resumed.step()['brightdata'], 'campaign_running')
        self.assertEqual(client.starts, 1)
        client.state = 'ready'
        resumed.last_poll = None
        result = resumed.step()
        self.assertEqual(result['brightdata'], 'campaign_batch_complete')
        self.assertEqual(result['new_unique'], 1)
        self.assertEqual(client.downloads, 1)

    def test_legacy_inflight_reservation_resumes_with_new_geography_without_trigger(self):
        archive = Archive(self.path)
        cfg = config()
        scope = dict(country='IT', city='Milano', adapter=LEGACY_VERSION, configuration=cfg,
                     max_price_exclusive_eur=20000, available_only=True)
        archive.ingest(dict(source=CONTROL, run_id='brightdata-trial1', page_id='reservation',
            mode='initial', scope=scope, input_cursor=None, next_cursor='reserved', complete=False, records=[]))
        archive.ingest(dict(source=CONTROL, run_id='brightdata-trial1', page_id='sd_test123',
            mode='initial', scope=scope, input_cursor='reserved', next_cursor=None, complete=True, records=[]))
        client = FakeClient(); client.state = 'ready'; client.rows = [dict(car(), location='Bergamo, Italia')]
        result = cycle(cfg, archive, client=client, free_confirmed=True)
        self.assertEqual(result['collection']['accepted'], 1)
        self.assertEqual(client.starts, 0)
        self.assertEqual(archive.history('facebook_marketplace', '123')[0]['payload']['province'], 'BG')
        changed = dict(cfg, limit=9)
        with self.assertRaises(ValueError):
            cycle(changed, archive, client=client, free_confirmed=True)
        archive.close()

    def test_background_replay_recovers_existing_snapshots_once_and_keeps_freshness(self):
        archive = Archive(self.path); client = FakeClient(); client.state = 'ready'
        client.rows = [dict(car(), location='Brescia, Italia')]
        with patch('deal_finder.brightdata.event', side_effect=ValueError('old geography')):
            cycle(config(), archive, client=client, free_confirmed=True)
        stamp = archive.db.execute("SELECT received_at FROM collection_pages WHERE page_id='reservation'").fetchone()[0]
        replay = BackgroundArchiveRevalidation(self.path)
        result = replay.step()
        self.assertEqual((result['new_unique'], result['total_unique'], result['provider_requests']), (1, 1, 0))
        saved = archive.history('facebook_marketplace', '123')[0]
        self.assertEqual((saved['observed_at'], saved['payload']['province']), (stamp, 'BS'))
        self.assertEqual(client.starts, 1)
        self.assertEqual(credit_ledger(archive), 1)
        self.assertEqual(BackgroundArchiveRevalidation(self.path).step()['brightdata'], 'archive_revalidation_complete')
        self.assertEqual(len(archive.history('facebook_marketplace', '123')), 1)
        self.assertEqual(archive.run_status('facebook_marketplace', 'brightdata-sd_test123')['quarantined'], 1)
        archive.close()

    def test_credit_ceiling_includes_previous_trials_and_final_batch(self):
        archive = Archive(self.path); trial = FakeClient(); trial.state = 'ready'; trial.rows *= 3
        with patch.object(trial, 'start', return_value='sd_prior123'):
            cycle(config(), archive, client=trial, free_confirmed=True)
        self.assertEqual(credit_ledger(archive), 3)
        archive.close()
        client = FakeClient(); client.rows = [car(), car()]; client.state = 'ready'
        campaign = BackgroundCampaign(self.path, spec(ceiling=5, batch=10), client=client)
        with patch.dict('os.environ',{'DEAL_FINDER_BRIGHTDATA_NEXT_BATCH_LIMIT':'100'}), patch.object(client, 'start', wraps=client.start) as start:
            self.assertEqual(campaign.step()['credit_units_reserved_or_returned'], 5)
            self.assertEqual(start.call_args.args[0]['limit'], 2)
        campaign.last_poll = None
        self.assertEqual(campaign.step()['reason'], 'credit_ceiling_reached')
        self.assertEqual(client.starts, 1)

    def test_inflight_batch_limit_is_preserved_after_speed_change(self):
        client=FakeClient()
        campaign=BackgroundCampaign(self.path,spec(ceiling=200,batch=10),client=client)
        campaign.step()
        with patch.dict('os.environ',{'DEAL_FINDER_BRIGHTDATA_NEXT_BATCH_LIMIT':'100'}):
            resumed=BackgroundCampaign(self.path,spec(ceiling=200,batch=10),client=client)
            resumed.step()
        self.assertEqual(client.starts,1)
        archive=Archive(self.path)
        self.assertEqual(credit_ledger(archive),10)
        archive.close()

    def test_uncertain_trigger_keeps_reservation_and_never_reposts(self):
        client = FakeClient()
        with patch.object(client, 'start', side_effect=ProviderError('secret')) as start:
            first = BackgroundCampaign(self.path, spec(), client=client)
            self.assertEqual(first.step()['brightdata'], 'paused')
            resumed = BackgroundCampaign(self.path, spec(), client=client)
            self.assertEqual(resumed.step()['brightdata'], 'campaign_paused')
            restarted = BackgroundCampaign(self.path, spec(), client=client)
            self.assertEqual(restarted.step()['reason'], 'recovery_required')
            self.assertEqual(start.call_count, 1)
        archive = Archive(self.path)
        self.assertEqual(credit_ledger(archive), 10)
        archive.close()

    def test_sparse_results_reclaim_confirmed_unused_cap(self):
        client = FakeClient(); client.state = 'ready'
        campaign = BackgroundCampaign(self.path, spec(ceiling=10, batch=10), client=client)
        self.assertEqual(campaign.step()['credit_units_reserved_or_returned'], 1)
        archive = Archive(self.path)
        self.assertEqual(credit_ledger(archive), 1)
        archive.close()

    def test_plan_balances_brands_prices_and_does_not_repeat_urls(self):
        plan = search_plan()
        self.assertGreater(len(plan), 250)
        urls = [config_for(spec(), i, 10)['input'][0]['url'] for i in range(len(plan))]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertGreater(len({x['query'].split()[0] for x in plan[:100]}), 15)
        for entry in plan:
            self.assertLess(entry['high'], 20000)
        self.assertIn('opportunity_discovery', {x['purpose'] for x in plan})

    def test_invalid_spec_cannot_enable_unbounded_spending(self):
        for change in ({'credit_ceiling': 5001}, {'credit_ceiling': True}, {'batch_limit': 101},
                       {'plan_version': 'unknown'}, {'campaign_id': 'bad/path'}):
            with self.assertRaises(ValueError):
                validate_spec(dict(spec(), **change))

    def test_historical_revalidation_does_not_double_count_provider_credits(self):
        archive = Archive(self.path); client = FakeClient(); client.state = 'ready'
        result = cycle(config(), archive, client=client, free_confirmed=True)
        from deal_finder.brightdata import revalidate_quarantine
        revalidate_quarantine(archive, result['snapshot_id'])
        self.assertEqual(credit_ledger(archive), 1)
        archive.close()

    def test_changed_campaign_config_rejected_before_provider_call(self):
        client = FakeClient()
        BackgroundCampaign(self.path, spec(), client=client).step()
        changed = BackgroundCampaign(self.path, spec(ceiling=90), client=client)
        self.assertEqual(changed.step()['brightdata'], 'paused')
        self.assertEqual(client.starts, 1)

    def test_duplicate_exhaustion_pauses_and_survives_restart(self):
        class DistinctSnapshots(FakeClient):
            def start(self, config):
                self.starts += 1
                return 'sd_unique' + str(self.starts)
        client = DistinctSnapshots(); client.state = 'ready'
        campaign = BackgroundCampaign(self.path, spec(ceiling=100, batch=10), client=client)
        for _ in range(6):
            campaign.last_poll = None
            result = campaign.step()
        self.assertEqual(result['brightdata'], 'campaign_paused')
        self.assertEqual(result['new_unique'], 1)
        resumed = BackgroundCampaign(self.path, spec(ceiling=100, batch=10), client=client)
        self.assertEqual(resumed.step()['reason'], 'five_nonempty_batches_without_new_valid_cars')
        self.assertEqual(client.starts, 6)

    def test_transient_get_retries_survive_restart_without_new_trigger(self):
        client = FakeClient()
        with patch.object(client, 'progress', side_effect=ProviderError('secret', http_status=503, phase='progress')):
            result = BackgroundCampaign(self.path, spec(), client=client).step()
            self.assertEqual(result['retry'], 1)
            result = BackgroundCampaign(self.path, spec(), client=client).step()
            self.assertEqual(result['retry'], 2)
        client.state = 'ready'
        self.assertEqual(BackgroundCampaign(self.path, spec(), client=client).step()['brightdata'], 'campaign_batch_complete')
        self.assertEqual(client.starts, 1)
