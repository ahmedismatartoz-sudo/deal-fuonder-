import os
import tempfile
import unittest
from datetime import timedelta
from test_core import NOW
from test_archive import page
from test_market import complete
from deal_finder.archive import Archive
from deal_finder.price_memory import PriceMemory
from deal_finder.queue import Queue
from unittest.mock import Mock, patch
from deal_finder.price_memory import BackgroundPriceMemory
from deal_finder.price_memory import risky, same_variant

class PriceMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.tmp.name,'archive.db')
        self.archive=Archive(self.path)
        self.memory=PriceMemory(self.archive.db)
    def tearDown(self):
        self.archive.close();self.tmp.cleanup()
    def ingest(self,records,run='test',now=NOW):
        self.archive.ingest(page(records,run=run),as_of=now)
        while self.memory.sync(now): pass
    def test_compact_incremental_memory_invalidates_removed_price(self):
        p=complete('one',price_eur=10000)
        p['payload']['original_large']='x'*100000
        self.ingest([p])
        self.assertEqual(self.memory.sync(NOW),0)
        self.assertNotIn('original_large',str(next(self.memory.current(NOW))))
        later=NOW+timedelta(seconds=1)
        self.ingest([complete('one',active=False,observed_at=later.isoformat())],run='removed',now=later)
        self.assertEqual(list(self.memory.current(later)),[])
        self.assertEqual(len(list(self.memory.current(NOW))),1)
    def test_mixed_price_bands_are_only_research_with_unknown_net_margin(self):
        records=[]
        for band,(cheap,typical) in enumerate([(2000,8000),(6000,14000),(11000,22000),(16000,30000)]):
            common=dict(model='model'+str(band),fuel='diesel',transmission='manual',condition='unknown')
            records.append(complete('cheap'+str(band),price_eur=cheap,mileage_km=80000,**common))
            for i in range(10):
                records.append(complete('peer'+str(band)+'-'+str(i),price_eur=typical+i*10,mileage_km=80000+i,**common))
        self.ingest(records)
        report=self.memory.first_test('first',NOW)
        self.assertEqual({p['price_band'] for p in report['candidates']},{0,1,2,3})
        self.assertEqual(report['approved_buys'],0)
        for p in report['candidates']:
            self.assertIsNone(p['net_margin_eur'])
            self.assertFalse(p['buy_recommendation'])
            self.assertIn('damage_inspection',p['blocking_reasons'])
        self.assertEqual(self.memory.first_test('first',NOW+timedelta(days=1)),report)
    def test_financing_damage_stale_and_exactly_copied_analogies_excluded(self):
        records=[complete('cheap',price_eur=1000,condition='unknown')]
        records += [complete('peer'+str(i),price_eur=15000,condition='unknown') for i in range(10)]
        records += [complete('damaged',price_eur=1000,condition='damaged'),
                    complete('deposit',price_eur=1000,price_kind='deposit')]
        self.ingest(records)
        report=self.memory.first_test('excluded',NOW)
        self.assertEqual(report['candidates'],[])
        self.assertEqual(report['exclusions']['damage_signal_or_unknown_damaged_scope'],1)
        self.assertEqual(report['exclusions']['price_or_family_missing'],1)

    def test_exploratory_profile_finds_sparse_variant_without_claiming_profit(self):
        records=[complete('cheap',price_eur=5000,version_text='1.2 Easy',year=2018,mileage_km=80000)]
        records += [complete('peer'+str(i),price_eur=8000+i*100,version_text='1.2 Easy',
                             year=2020,mileage_km=110000+i) for i in range(3)]
        records += [complete('wrong-engine'+str(i),price_eur=20000,version_text='1.6 ST') for i in range(9)]
        self.ingest(records)
        strict=self.memory.first_test('profiles',NOW)
        exploratory=self.memory.first_test('profiles',NOW,profile='exploratory')
        self.assertEqual(strict['candidates'],[])
        self.assertEqual(len(exploratory['candidates']),1)
        lead=exploratory['candidates'][0]
        self.assertEqual(lead['source_id'],'cheap')
        self.assertEqual(lead['comparable_count'],3)
        self.assertEqual(lead['gross_headroom_before_all_costs_eur'],3000)
        self.assertIsNone(lead['net_margin_eur'])
        self.assertFalse(lead['buy_recommendation'])
        self.assertEqual(exploratory['approved_buys'],0)
        self.assertEqual(exploratory['screening_policy']['profile'],'exploratory')
        self.assertEqual(self.memory.first_test('profiles',NOW,profile='exploratory'),exploratory)
    def test_partial_projection_does_not_create_complete_test(self):
        self.archive.ingest(page([complete('new')]),as_of=NOW)
        self.assertEqual(self.memory.first_test('pending',NOW)['status'],'waiting_for_price_memory')
        self.assertEqual(self.archive.db.execute('SELECT count(*) FROM price_test_reports').fetchone()[0],0)
    def test_forward_projection_revisits_backfilled_keys_before_completion(self):
        self.archive.ingest(page([complete('z-last')]),as_of=NOW)
        self.assertEqual(self.memory.sync(NOW,limit=1),1)
        cursor=self.memory.next_cursor
        self.archive.ingest(page([complete('a-late')],run='backfill'),as_of=NOW)
        self.assertEqual(self.memory.sync(NOW,after=cursor),0)
        self.assertIsNone(self.memory.next_cursor)
        self.assertTrue(self.memory.pending(NOW))
        self.assertEqual(self.memory.first_test('backfill',NOW)['status'],'waiting_for_price_memory')
        self.assertEqual(self.memory.sync(NOW,after=self.memory.next_cursor),1)
        self.assertFalse(self.memory.pending(NOW))
    def test_priority_batch_cannot_claim_other_jobs(self):
        q=Queue(self.path)
        try:
            q.submit('ordinary',[dict(listing=complete('ordinary')['payload'])])
            q.submit('first-test',[dict(listing=complete('priority')['payload'])])
            job=q.claim(batch_id='first-test')
            self.assertEqual(job['raw']['listing']['source_id'],'priority')
            self.assertIsNone(q.claim(batch_id='first-test'))
            self.assertEqual(q.claim()['raw']['listing']['source_id'],'ordinary')
        finally:q.close()

    def test_autonomous_refreshes_new_data_and_does_not_repeat_unchanged_vehicle_jobs(self):
        from deal_finder.price_memory import priority_batch_prefix
        records=[complete('cheap',price_eur=1000,version_text='1.2 Easy')]
        records += [complete('peer'+str(i),price_eur=10000+i*10,version_text='1.2 Easy') for i in range(9)]
        self.ingest(records)
        with patch.dict(os.environ,{'DEAL_FINDER_AUTONOMOUS_SCREENING_ENABLED':'1'}), \
             patch('deal_finder.price_memory.datetime') as clock:
            clock.now.return_value=NOW
            first=BackgroundPriceMemory(self.path).step('continuous')
            same=BackgroundPriceMemory(self.path).step('continuous')
            self.assertEqual(first['run_id'],same['run_id'])
            later=NOW+timedelta(seconds=1)
            self.ingest([complete('new',model='another',observed_at=later.isoformat())],run='new',now=later)
            clock.now.return_value=later
            changed=BackgroundPriceMemory(self.path).step('continuous')
            self.assertNotEqual(first['run_id'],changed['run_id'])
            clock.now.return_value=NOW+timedelta(days=1)
            tomorrow=BackgroundPriceMemory(self.path).step('continuous')
            self.assertNotEqual(changed['run_id'],tomorrow['run_id'])
            q=Queue(self.path)
            try:
                self.assertEqual(q.db.execute('SELECT count(*) FROM batches').fetchone()[0],1)
                self.assertIsNotNone(q.claim(batch_prefix=priority_batch_prefix()))
                self.assertIsNone(q.claim(batch_prefix=priority_batch_prefix()))
            finally:q.close()

    def test_queue_prefix_treats_wildcards_literally(self):
        q=Queue(self.path)
        try:
            q.submit('prefix-one',[dict(listing=complete('one')['payload'])])
            q.submit('prefix_two',[dict(listing=complete('two')['payload'])])
            self.assertIsNone(q.claim(batch_prefix='prefix%'))
            self.assertIsNotNone(q.claim(batch_prefix='prefix_'))
            self.assertIsNone(q.claim(batch_prefix='prefix_'))
        finally:q.close()

    def test_export_only_and_non_registrable_cars_are_excluded(self):
        self.ingest([complete('export-only',price_eur=1000,description='Vendita esclusivamente per esportazione fuori dall’Unione Europea. Veicolo non immatricolabile.')])
        report=self.memory.first_test('restrictions',NOW,profile='exploratory')
        self.assertEqual(report['candidates'],[])
        self.assertEqual(report['exclusions']['export_or_registration_restriction'],1)

    def test_configuration_resume_enqueues_once_without_reusing_blocked_jobs(self):
        from deal_finder.price_memory import priority_batch_id
        records=[complete('cheap',price_eur=1000,version_text='1.2 Easy')]
        records += [complete('peer'+str(i),price_eur=10000+i*10,version_text='1.2 Easy') for i in range(9)]
        self.ingest(records)
        with patch('deal_finder.price_memory.datetime') as clock, \
             patch('deal_finder.agent_runtime.connections') as connections:
            clock.now.return_value=NOW
            connections.return_value=dict(photo_web_provider_configured=False)
            blocked=priority_batch_id('resume')
            BackgroundPriceMemory(self.path).step('resume')
            BackgroundPriceMemory(self.path).step('resume')
            connections.return_value=dict(photo_web_provider_configured=True)
            ready=priority_batch_id('resume')
            BackgroundPriceMemory(self.path).step('resume')
            BackgroundPriceMemory(self.path).step('resume')
        self.assertNotEqual(blocked,ready)
        q=Queue(self.path)
        try:
            self.assertEqual(q.db.execute('SELECT count(*) FROM batches').fetchone()[0],2)
            self.assertIsNotNone(q.claim(batch_id=blocked))
            self.assertIsNotNone(q.claim(batch_id=ready))
            self.assertIsNone(q.claim(batch_id=ready))
        finally:q.close()

    def test_real_description_damage_signals_cannot_enter_clean_first_test(self):
        for text in ('motore da cambiare, carrozzeria scolorita sul cofano e tetto',
                     'GRANDINATA SU FIANCO DX, TETTO, COFANO',
                     'segni di grandine e una leggera crepa sul parafango davanti'):
            self.assertTrue(risky(dict(description=text, condition='unknown')), text)
        self.assertFalse(risky(dict(description='Mai incidentata. Non grandinata.',condition='unknown')))

    def test_same_family_cannot_mix_performance_versions_or_engines(self):
        self.assertFalse(same_variant(dict(version_text='1.0 Active'),dict(version_text='GR 1.6 Circuit')))
        self.assertFalse(same_variant(dict(version_text='3p 1.0 ecoboost 100cv'),dict(version_text='3p 1.6 ST 182cv')))
        self.assertFalse(same_variant(dict(version_text='1.2 Easy',power_hp=69),dict(version_text='1.2 Easy',power_hp=85)))
        self.assertFalse(same_variant({},{}))
        self.assertTrue(same_variant(dict(version_text='1.2 Easy'),dict(version_text=' 1.2 EASY ')))
        base=dict(model='panda',power_hp=69,displacement_cc=1242,year=2020)
        self.assertTrue(same_variant(dict(base,version_text='1.2 Easy 69cv'),
                                     dict(base,version_text='Panda III 2016 1.2 Easy 69cv')))
        self.assertFalse(same_variant(dict(base,version_text='1.2 Easy 69cv'),
                                      dict(base,version_text='Panda 1.2 Lounge 69cv')))
        self.assertFalse(same_variant(dict(base,version_text='Panda II 2011 1.2 Easy 69cv'),
                                      dict(base,version_text='Panda III 2016 1.2 Easy 69cv')))

    def test_yaris_active_does_not_become_deal_against_gr_prices(self):
        records=[complete('active',model='yaris',price_eur=13600,mileage_km=36910,
                          version_text='1.0 Active')]
        records += [complete('gr'+str(i),model='yaris',price_eur=33500+i*100,
                            mileage_km=36910+i,version_text='GR 1.6 Circuit') for i in range(9)]
        self.ingest(records)
        self.assertEqual(self.memory.first_test('no-gr-confusion',NOW)['candidates'],[])

    def test_source_version_and_engine_fields_survive_enrichment_handoff(self):
        from deal_finder.agents.enrichment import listing_input
        payload=complete('car',version_text='1.3 mjt Pop 85cv',power_hp=84,displacement_cc=1248)['payload']
        listing=listing_input('export','car',NOW.isoformat(),payload['url'],payload)
        self.assertEqual(listing['version_text'],'1.3 mjt Pop 85cv')
        self.assertEqual(listing['power_hp'],84)
        self.assertEqual(listing['displacement_cc'],1248)

    def test_existing_compact_memory_recovers_only_retained_scalar_source_facts(self):
        record=complete('retained')
        record['payload'].pop('seller_type')
        record['payload']['original']=dict(seller=dict(type='PrivateSeller'),
            vehicle=dict(rawPowerInHp=69,rawCylinderCapacity=1242),unused_large='x'*100000)
        self.archive.ingest(page([record],source='autoscout24'),as_of=NOW)
        self.memory.sync(NOW)
        value=next(self.memory.current(NOW))
        self.assertEqual(value['seller_type'],'private')
        self.assertEqual(value['power_hp'],69)
        self.assertEqual(value['displacement_cc'],1242)
        self.assertNotIn('unused_large',str(value))

    def test_old_report_is_recomputed_instead_of_returning_invalid_candidates(self):
        from deal_finder.archive import canonical
        with self.archive.db:
            self.archive.db.execute('INSERT INTO price_test_reports VALUES (?,?,?)',
                ('old',NOW.isoformat(),self.archive.db.json_param(canonical(dict(candidates=[dict(title='bad analogy')])))))
        report=self.memory.first_test('old',NOW)
        self.assertEqual(report['candidates'],[])
        self.assertIn('screening_version',report)

    def test_legacy_source_cache_warms_once_and_comparisons_never_read_originals(self):
        record=complete('cached')
        record['payload']['original']=dict(vehicle=dict(rawPowerInHp=69,rawCylinderCapacity=1242))
        self.archive.ingest(page([record],source='autoscout24'),as_of=NOW)
        self.memory.sync(NOW)
        self.archive.db.execute('DELETE FROM identity_source_cache')
        self.assertTrue(self.memory.pending(NOW))
        self.assertEqual(list(self.memory.current(NOW)),[])
        self.assertEqual(self.memory.sync(NOW,limit=1),1)
        self.assertFalse(self.memory.pending(NOW))
        self.assertEqual(self.memory.sync(NOW),0)
        statements=[]
        original_execute=self.archive.db.execute
        def traced(sql,*args,**kwargs):
            statements.append(sql)
            return original_execute(sql,*args,**kwargs)
        with patch.object(self.archive.db,'execute',side_effect=traced):
            first=list(self.memory.current(NOW))
            second=list(self.memory.current(NOW))
        self.assertEqual(first,second)
        self.assertEqual(first[0]['power_hp'],69)
        self.assertFalse(any('listing_events' in sql for sql in statements))

class BackgroundPriceMemoryTests(unittest.TestCase):
    def test_slow_success_reduces_work_and_fast_success_is_capped(self):
        clock=[0]
        memory=Mock(pending=Mock(return_value=True))
        def sync(*args,**kwargs):
            clock[0]+=6
            return kwargs['limit']
        memory.sync.side_effect=sync
        archive=Mock()
        worker=BackgroundPriceMemory('unused')
        with patch('deal_finder.archive.Archive',return_value=archive),patch('deal_finder.price_memory.PriceMemory',return_value=memory),patch('time.monotonic',side_effect=lambda:clock[0]):
            result=worker.step()
            self.assertEqual(result['projected_this_step'],25)
            self.assertEqual(result['next_projection_batch'],12)
            self.assertEqual(result['projection_seconds'],6)
            def fast_sync(*args,**kwargs):
                clock[0]+=1
                return kwargs['limit']
            memory.sync.side_effect=fast_sync
            worker.batch_size=100
            self.assertEqual(worker.step()['next_projection_batch'],100)
        self.assertEqual(archive.close.call_count,2)

    def test_query_failure_backs_off_without_opening_another_connection(self):
        import psycopg
        clock=[0]
        memory=Mock()
        memory.sync.side_effect=psycopg.errors.QueryCanceled('private query detail')
        archive=Mock()
        worker=BackgroundPriceMemory('unused')
        with patch('deal_finder.archive.Archive',return_value=archive) as opened,patch('deal_finder.price_memory.PriceMemory',return_value=memory),patch('time.monotonic',side_effect=lambda:clock[0]):
            result=worker.step()
            self.assertEqual(result['db_sqlstate'],'57014')
            self.assertEqual(result['next_projection_batch'],12)
            self.assertEqual(result['retry_after_seconds'],2)
            self.assertNotIn('private query detail',str(result))
            clock[0]=1
            self.assertIsNone(worker.step())
            self.assertEqual(opened.call_count,1)
            clock[0]=2
            result=worker.step()
            self.assertEqual(result['retry_after_seconds'],4)
            self.assertEqual(opened.call_count,2)
        self.assertEqual(archive.close.call_count,2)


class StreamingCadenceTests(unittest.TestCase):
    @patch.dict(os.environ,{'DEAL_FINDER_FIRST_TEST_PROFILE':'discovery','DEAL_FINDER_AUTONOMOUS_SCREENING_ENABLED':'1'})
    def test_new_prices_project_between_reports_without_waiting_for_identity(self):
        clock=[0]
        memory=Mock();memory.sync.return_value=25;memory.next_cursor=None
        memory.first_test.return_value=dict(status='completed_research_test',candidates=[])
        memory.automatic_run_id.return_value='auto'
        archive=Mock();worker=BackgroundPriceMemory('unused')
        with patch('deal_finder.archive.Archive',return_value=archive),patch('deal_finder.price_memory.PriceMemory',return_value=memory),patch('time.monotonic',side_effect=lambda:clock[0]):
            worker.step()
            clock[0]=2;worker.step()
            self.assertEqual(memory.sync.call_count,2)
            self.assertEqual(memory.first_test.call_count,1)
            memory.pending.assert_not_called()
            self.assertFalse(memory.sync.call_args.kwargs['recover_identity'])
            clock[0]=61;worker.step()
            self.assertEqual(memory.first_test.call_count,2)
