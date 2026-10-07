import os
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from deal_finder.collection_price_agent import CollectionPriceAgent
from deal_finder.autoscout24 import collect, cycle_run_id
import test_autoscout24 as fixtures
from test_autoscout24 import FakeClient, config, item, detail, BASE


class CollectionPriceTests(unittest.TestCase):
    setUp = fixtures.AutoScoutTests.setUp
    tearDown = fixtures.AutoScoutTests.tearDown
    def seed_market(self):
        from deal_finder.price_memory import PriceMemory
        records=[]
        for i in range(3):
            row=detail('peer'+str(i));row['prices']['public']['priceRaw']=10000+i*500
            row['vehicle']['mileageInKmRaw']=80000+i*1000
            from deal_finder.autoscout24 import record_event
            records.append(record_event(row,url=BASE+row['url'],observed_at=datetime.now(timezone.utc).isoformat(),detailed=True))
        self.archive.ingest(dict(source='autoscout24',run_id='peers',page_id='0',mode='initial',scope={'country':'IT'},records=records,complete=True))
        memory=PriceMemory(self.archive.db)
        memory.sync(datetime.now(timezone.utc))

    @patch.dict(os.environ,{'DEAL_FINDER_AUTOSCOUT24_PRICE_SCREENING_ENABLED':'1'})
    def test_only_apparent_opportunity_gets_detail_others_keep_search_prices(self):
        self.seed_market()
        low=item('cheap');low['price']['priceRaw']=5000
        ordinary=item('ordinary');ordinary['price']['priceRaw']=10000
        client=FakeClient([[low,ordinary]])
        result=collect(config(),self.archive,run_id='screen',client=client)
        self.assertEqual(result['accepted'],2)
        self.assertEqual(len([c for c in client.calls if c[1]=='listing']),1)
        candidate=self.archive.history('autoscout24','cheap')[0]['payload']
        normal=self.archive.history('autoscout24','ordinary')[0]['payload']
        self.assertEqual(candidate['collection_price_screen']['status'],'apparent_opportunity')
        self.assertTrue(candidate['detail_fetched'])
        self.assertFalse(normal['detail_fetched'])
        self.assertEqual(normal['price_eur'],10000)
        self.assertIsNone(candidate['collection_price_screen']['net_margin_eur'])
        self.assertFalse(candidate['collection_price_screen']['buy_recommendation'])

    @patch.dict(os.environ,{'DEAL_FINDER_AUTOSCOUT24_PRICE_SCREENING_ENABLED':'1'})
    def test_known_price_change_is_not_skipped_and_unknown_market_is_preserved(self):
        client=FakeClient([[item()]])
        collect(config(),self.archive,run_id='first',client=client)
        changed=item();changed['price']['priceRaw']=4000
        client=FakeClient([[changed]])
        collect(config(),self.archive,run_id='changed',client=client)
        history=self.archive.history('autoscout24','car1',limit=10)
        self.assertEqual(len(history),2)
        self.assertEqual(history[0]['payload']['price_eur'],4000)
        self.assertEqual(history[0]['payload']['collection_price_screen']['status'],'needs_market_evidence')
        self.assertEqual(len(client.calls),1)
        collect(config(),self.archive,run_id='same',client=FakeClient([[changed]]))
        self.assertEqual(len(self.archive.history('autoscout24','car1',limit=10)),2)

    @patch.dict(os.environ,{'DEAL_FINDER_AUTOSCOUT24_PRICE_SCREENING_ENABLED':'1'})
    def test_deferred_search_can_be_promoted_when_comparables_arrive(self):
        cheap=item('cheap');cheap['price']['priceRaw']=5000
        collect(config(),self.archive,run_id='deferred',client=FakeClient([[cheap]]))
        self.seed_market()
        client=FakeClient([[cheap]])
        collect(config(),self.archive,run_id='retry',client=client)
        self.assertEqual(len(client.calls),2)
        self.assertTrue(self.archive.history('autoscout24','cheap')[0]['payload']['detail_fetched'])

    def test_screening_version_changes_incremental_signature_only(self):
        with patch.dict(os.environ,{'DEAL_FINDER_AUTOSCOUT24_PRICE_SCREENING_ENABLED':'0'}):
            initial=cycle_run_id(config(),'initial');incremental=cycle_run_id(config(),'incremental')
        with patch.dict(os.environ,{'DEAL_FINDER_AUTOSCOUT24_PRICE_SCREENING_ENABLED':'1'}):
            self.assertEqual(initial,cycle_run_id(config(),'initial'))
            self.assertNotEqual(incremental,cycle_run_id(config(),'incremental'))

    def test_completed_legacy_base_is_reused_without_network_requests(self):
        from deal_finder.autoscout24 import validate_config
        self.archive.ingest(dict(source='autoscout24',run_id='native-base',page_id='0',mode='initial',
            scope=dict(country='IT',adapter='autoscout24-public-html-v3',configuration=validate_config(config()),purpose='initial_base',snapshot_consistent=False),records=[],complete=True))
        client=FakeClient([[]])
        self.assertTrue(collect(config(),self.archive,run_id='base',mode='initial',client=client)['complete'])
        self.assertEqual(client.calls,[])

    @patch.dict(os.environ,{'DEAL_FINDER_AUTOSCOUT24_PRICE_SCREENING_ENABLED':'1'})
    def test_background_collection_resumes_and_stops_on_access_denial(self):
        from deal_finder.autoscout24 import validate_config, CollectionBlocked
        from deal_finder.bootstrap import OpportunityCollection
        cfg=validate_config(config())
        self.archive.ingest(dict(source='autoscout24',run_id='native-base',page_id='0',mode='initial',
            scope=dict(country='IT',adapter='autoscout24-public-html-v3',configuration=cfg,purpose='initial_base',snapshot_consistent=False),records=[],complete=True))
        spec=dict(run_id='base',config=cfg)
        client=FakeClient([[item('a')],[item('b')]])
        with patch('deal_finder.autoscout24.PublicClient',return_value=client),patch('deal_finder.autoscout24.time.sleep'):
            first=OpportunityCollection(self.path,spec).step()
            self.assertEqual(first['opportunity_collection'],'collecting')
            restart=OpportunityCollection(self.path,spec)
            final=restart.step()
            self.assertEqual(final['opportunity_collection'],'complete')
            self.assertEqual(first['run_id'],final['run_id'])
            self.assertEqual(final['accepted'],2)
        blocked=OpportunityCollection(self.path,spec)
        blocked.run_id='new-denied-run'
        with patch('deal_finder.autoscout24.PublicClient') as factory,patch('deal_finder.autoscout24.time.sleep'):
            factory.return_value.get.side_effect=CollectionBlocked('HTTP 403')
            self.assertEqual(blocked.step()['opportunity_collection'],'paused')
            self.assertIsNone(blocked.step())
            self.assertEqual(factory.return_value.get.call_count,1)
