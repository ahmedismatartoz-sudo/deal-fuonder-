import copy
import os
import tempfile
import unittest
from unittest.mock import patch, Mock
from test_core import NOW, row
from test_archive import page, event
from test_market import complete
from test_agents import envelope, pool, analyze
from deal_finder.market import Market
from deal_finder.queue import Queue
from deal_finder.agent_runtime import BackgroundScreening, connections


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'runtime.db')
        self.market, self.queue = Market(self.path), Queue(self.path)

    def tearDown(self):
        self.queue.close(); self.market.close(); self.tmp.cleanup()

    def test_incomplete_ads_are_organized_without_paid_calls_or_attestations(self):
        original = event('partial', payload=dict(title='Fiat Panda', image_urls=['https://example.com/front.jpg']))
        self.market.archive.ingest(page([original]), as_of=NOW)
        self.market.sync(NOW)
        self.assertEqual(self.market.enqueue_enrichment(self.queue, as_of=NOW), 1)
        self.assertEqual(self.market.enqueue_enrichment(self.queue, as_of=NOW), 0)
        with patch('deal_finder.agents.photo_identity.research', side_effect=AssertionError('No paid call')):
            result = self.queue.work_one()
        self.assertEqual(result['state'], 'done')
        out = self.queue.result(result['job_id'])['run']['outputs']['enrichment']
        self.assertIn('generation', out['missing_fields'])
        self.assertIn('total_asking_price', out['missing_fields'])
        self.assertFalse(out['identity_attestation'])
        self.assertFalse(out['repair_search_started'])
        self.assertEqual(out['tasks'][1]['name'], 'rescreen_market')

    def test_newer_source_snapshot_invalidates_enrichment(self):
        self.market.archive.ingest(page([event('partial')]), as_of=NOW)
        self.market.sync(NOW)
        self.market.enqueue_enrichment(self.queue, as_of=NOW)
        from datetime import timedelta
        newer = event('partial', observed_at=(NOW+timedelta(seconds=1)).isoformat())
        self.market.archive.ingest(page([newer], run='updated'), as_of=NOW+timedelta(seconds=2))
        result = self.queue.work_one()
        self.assertEqual(self.queue.result(result['job_id'])['run']['outputs']['enrichment']['status'], 'superseded')

    def test_enrichment_recovers_retained_source_without_refetch_or_paid_research(self):
        from test_autoscout24 import detail, BASE
        from deal_finder.autoscout24 import record_event
        from deal_finder.agents.enrichment import execute
        original=detail('retained')
        original['seller']['type']='PrivateSeller'
        original['vehicle'].update(rawPowerInHp=69,rawCylinderCapacity=1242)
        observation=record_event(original,url=BASE+original['url'],observed_at=NOW.isoformat(),detailed=True)
        # Simulate an old projection that discarded these source fields.
        observation['payload'].pop('seller_type')
        observation['payload'].pop('power_hp')
        observation['payload'].pop('displacement_cc')
        self.market.archive.ingest(page([observation],source='autoscout24'),as_of=NOW)
        with patch('deal_finder.agents.photo_identity.research',side_effect=AssertionError('No paid call')):
            result=execute(dict(listing=dict(source='autoscout24',source_id='retained',
                observed_at=NOW.isoformat())),self.market.db,NOW)
        resolved=result['enrichment']['resolved_listing']
        self.assertEqual(resolved['seller_type'],'private')
        self.assertEqual(resolved['power_hp'],69)
        self.assertEqual(resolved['province'],'MI')
        self.assertEqual(resolved['version_text'],'1.2 Easy')
        self.assertFalse(result['validation']['data']['buy_recommendation'])

    def test_scheduler_handles_partial_collection_and_restart_without_duplicate_jobs(self):
        self.market.archive.ingest(page([complete(i) for i in range(8)]+[complete('cheap', price_eur=7000)]), as_of=NOW)
        runtime = BackgroundScreening(self.path)
        with patch('deal_finder.market.datetime') as clock:
            from datetime import datetime
            clock.now.return_value = NOW
            clock.fromisoformat.side_effect = datetime.fromisoformat
            first = runtime.step()
            second = BackgroundScreening(self.path).step()
        self.assertEqual(first['queued'], 1)
        self.assertEqual(second['queued'], 0)
        self.assertIsNone(runtime.step())

    def test_forged_enrichment_cannot_bypass_archive(self):
        self.queue.submit('forged', [dict(task='archive_enrichment', listing=dict(source='export', source_id='bad', observed_at=NOW.isoformat()))])
        self.assertEqual(self.queue.batch('forged')['quarantined_at_intake'], 1)
        self.assertEqual(self.queue.work_one()['state'], 'done')

    def test_connection_report_contains_no_keys_and_requires_opt_in(self):
        with patch.dict('os.environ', OPENAI_API_KEY='synthetic-secret', DEAL_FINDER_PARTS_MODEL='chosen',
                        DEAL_FINDER_PARTS_WEB_ENABLED='', DEAL_FINDER_PHOTO_IDENTITY_ENABLED=''):
            value = connections()
        self.assertFalse(value['parts_web_provider_configured'])
        self.assertNotIn('synthetic-secret', str(value))

    def test_photo_conflict_blocks_legacy_controls_before_repairs(self):
        with patch.dict('os.environ', DEAL_FINDER_PHOTO_IDENTITY_ENABLED='1'):
            with patch('deal_finder.agents.workflow.identify_photos', return_value=dict(conflicting_fields=['model'])):
                value = analyze(envelope(), pool(), NOW)
        self.assertEqual(value['condition']['status'], 'blocked')
        self.assertEqual(value['repair']['status'], 'blocked')
        self.assertEqual(value['opportunity']['status'], 'blocked')
        self.assertEqual(value['handoff']['route'], 'enrichment')

    def test_parts_accept_snapshot_reference_but_never_confirm_identity(self):
        from test_repair_research import request
        raw = envelope()
        raw['listing']['vehicle_id'] = None
        r = request([])
        r['vehicle'] = dict(source=raw['listing']['source'], source_id=raw['listing']['source_id'],
                            observed_at=raw['listing']['observed_at'], make='fiat', model='panda', generation='319',
                            year=2020, gearbox='manual')
        r['parts'][0]['identity_confirmed'] = True
        raw['parts_research'] = r
        value = analyze(raw, pool(), NOW)
        self.assertEqual(value['parts_research']['status'], 'provisional')
        self.assertEqual(value['parts_research']['data']['items'][0]['uncertainty'], 'wide')

    def test_family_triage_prioritizes_without_bypassing_exact_screening(self):
        from deal_finder.agents.market_triage import review
        records = [complete(i, price_eur=10000,version_text='1.2 Easy') for i in range(6)]
        cheap = complete('cheap', price_eur=5000,version_text='1.2 Easy')
        del cheap['payload']['trim']
        records.append(cheap)
        self.market.archive.ingest(page(records), as_of=NOW)
        value = dict(cheap['payload'], source='export', source_id='cheap')
        output = review(value, self.market.db, NOW)
        self.assertTrue(output['priority_enrichment'])
        self.assertFalse(output['candidate_for_verification'])
        self.assertEqual(output['observed_envelope_eur']['typical'], 10000)
        self.assertEqual(len(output['sources']), 6)
        self.assertFalse(output['calibrated'])

    def test_prioritized_enrichment_runs_configured_identity_research_and_keeps_it_provisional(self):
        from deal_finder.agents.enrichment import execute
        from deal_finder.agents.enrichment import listing_input
        records=[complete(i,price_eur=10000,version_text='1.2 Easy') for i in range(6)]
        cheap=complete('cheap',price_eur=5000,version_text='1.2 Easy',image_urls=['https://example.com/front.jpg'])
        records.append(cheap)
        self.market.archive.ingest(page(records),as_of=NOW)
        value=listing_input('export','cheap',cheap['observed_at'],cheap['url'],cheap['payload'])
        with patch('deal_finder.agent_runtime.connections',return_value=dict(photo_web_provider_configured=True)),\
             patch('deal_finder.agents.photo_identity.execute',return_value=dict(status='provisional_identification')) as research:
            result=execute(dict(listing=value),self.market.db,NOW)
        research.assert_called_once()
        self.assertEqual(result['enrichment']['research_execution']['status'],'provisional_identification')
        self.assertFalse(result['validation']['data']['buy_recommendation'])

    def test_financing_and_removed_newer_events_never_feed_family_triage(self):
        from deal_finder.agents.market_triage import review
        self.market.archive.ingest(page([complete(1, price_eur=1000, price_kind='installment'),
                                       complete(2, price_eur=1200, price_kind='unknown', description='Acconto richiesto'),
                                       complete(3, price_eur=10000)]), as_of=NOW)
        self.market.archive.ingest(page([complete(3, active=False, observed_at=NOW.isoformat())], run='removed'), as_of=NOW)
        output = review(dict(row('target'), source='export', source_id='target'), self.market.db, NOW)
        self.assertEqual(output['status'], 'needs_evidence')
        self.assertEqual(output['observation_count'], 0)

    def test_scheduler_failure_is_isolated_and_bounded_without_secret_logs(self):
        runtime = BackgroundScreening(self.path, interval=0)
        with patch('deal_finder.agent_runtime.Market', side_effect=RuntimeError('private connection secret')):
            results=[runtime.step() for _ in range(3)]
            self.assertIsNone(runtime.step())
        self.assertEqual(results[-1]['agent_scheduler'],'paused')
        self.assertNotIn('private connection secret',str(results))

    def test_newer_identical_content_gets_current_enrichment_reference(self):
        from datetime import timedelta
        self.market.archive.ingest(page([event('partial')]),as_of=NOW)
        self.market.sync(NOW)
        self.assertEqual(self.market.enqueue_enrichment(self.queue,as_of=NOW),1)
        fresh=NOW+timedelta(seconds=1)
        self.market.archive.ingest(page([event('partial',observed_at=fresh.isoformat())],run='refreshed'),as_of=fresh)
        self.market.sync(fresh)
        self.assertEqual(self.market.enqueue_enrichment(self.queue,as_of=fresh),1)
        self.assertEqual(self.market.enqueue_enrichment(self.queue,as_of=fresh),0)

    def test_candidate_catalog_filters_null_margins_and_current_source(self):
        from deal_finder.candidate_catalog import search
        from datetime import datetime, timezone
        self.market.archive.ingest(page([complete(i) for i in range(8)]+[complete('cheap',price_eur=7000)]),as_of=NOW)
        self.market.scan(as_of=NOW,queue=self.queue)
        self.assertEqual(self.queue.work_one()['state'],'done')
        now=datetime.now(timezone.utc)
        value=search(self.queue,make='fiat',min_price_eur=5000,max_price_eur=10000,as_of=now)
        self.assertEqual(value['count'],1)
        self.assertFalse(value['items'][0]['publishable'])
        self.assertEqual(search(self.queue,min_potential_gross_low_cents=0,as_of=now)['count'],0)
        self.market.archive.ingest(page([complete('cheap',active=False,observed_at=now.isoformat())],run='remove-candidate'),as_of=now)
        self.assertEqual(search(self.queue,as_of=now)['count'],0)
        with self.assertRaises(ValueError): search(self.queue,min_price_eur=-1,as_of=now)

    def test_large_archive_is_paged_without_losing_later_enrichment_or_family_evidence(self):
        from deal_finder.agents.market_triage import review
        from deal_finder.agents.enrichment import listing_input
        records=[complete(i,price_eur=10000,version_text='1.2 Easy') for i in range(205)]
        for record in records:
            del record['payload']['trim']
            record['payload']['large_unmapped_original']='x'*50000
        for index in range(0,len(records),100):
            self.market.archive.ingest(page(records[index:index+100],run='large-'+str(index)),as_of=NOW)
        self.market.sync(NOW)
        maximum=[0]
        execute=self.market.db.execute
        class BufferedRows:
            def __init__(self,rows): self.rows=rows
            def fetchall(self): return self.rows
            def fetchone(self): return self.rows[0] if self.rows else None
            def __iter__(self): return iter(self.rows)
        def buffering(sql,params=()):
            cursor=execute(sql,params)
            if sql.lstrip().upper().startswith('SELECT'):
                rows=cursor.fetchall()
                maximum[0]=max(maximum[0],len(rows))
                return BufferedRows(rows)
            return cursor
        with patch.object(self.market.db,'execute',side_effect=buffering):
            self.assertEqual(self.market.enqueue_enrichment(self.queue,as_of=NOW),100)
            self.assertEqual(self.market.enqueue_enrichment(self.queue,as_of=NOW),100)
            self.assertEqual(self.market.enqueue_enrichment(self.queue,as_of=NOW),5)
            target=dict(row('target',price_eur=5000,version_text='1.2 Easy'),source='export',source_id='target')
            self.assertEqual(review(target,self.market.db,NOW)['observation_count'],205)
        self.assertLessEqual(maximum[0],100)
        self.assertEqual(self.queue.db.execute('SELECT count(*) FROM jobs').fetchone()[0],205)
        value=self.queue.db.execute('SELECT payload FROM raw_records LIMIT 1').fetchone()[0]
        self.assertNotIn('large_unmapped_original',str(value))

    def test_candidate_paging_keeps_full_count_and_loads_only_page_cards(self):
        from deal_finder.candidate_catalog import search
        from deal_finder.agents.handoff import card
        from deal_finder.models import Listing
        from datetime import datetime,timezone
        records=[complete(i,price_eur=7000) for i in range(61)]
        self.market.archive.ingest(page(records),as_of=NOW)
        envelopes=[self.market.archive.normalized_envelope('export',str(i)) for i in range(61)]
        self.queue.submit('many-cards',envelopes)
        for i in range(61):
            job=self.queue.claim()
            target=Listing.parse(job['raw']['listing'])
            value=card(target,dict(route='verification'),{},dict(status='waiting'))
            value['potential_gross_low_cents']=i*100
            self.queue.finish(job,datetime.now(timezone.utc),[],dict(candidate_card=value,unrelated_large_analysis='x'*50000))
        result=search(self.queue,offset=50,limit=10)
        self.assertEqual(result['count'],61)
        self.assertEqual(len(result['items']),10)
        self.assertEqual(result['items'][0]['potential_gross_low_cents'],1000)
        self.assertNotIn('_run_id',result['items'][0])
        self.assertNotIn('unrelated_large_analysis',str(result))


class WorkerDrainTests(unittest.TestCase):
    def run_worker(self, queue, max_jobs, *, bootstrap=None, clock=None):
        import io
        from contextlib import redirect_stdout
        from deal_finder.worker import main
        env=dict(DEAL_FINDER_MODE='',DEAL_FINDER_BRIGHTDATA_CAMPAIGN='',DEAL_FINDER_BRIGHTDATA_CONFIG='',
                 DEAL_FINDER_AGENT_SCHEDULER_ENABLED='')
        with patch.dict('os.environ',env), patch('sys.argv',['worker','--db',':memory:','run','--max-jobs',str(max_jobs)]):
            with patch('deal_finder.worker.Queue',return_value=queue), patch('deal_finder.bootstrap.bootstrap_config',return_value={} if bootstrap is None else {'configured':True}):
                with patch('deal_finder.bootstrap.Bootstrap',return_value=bootstrap), patch('deal_finder.worker.time.monotonic',side_effect=clock or (lambda:0)):
                    with redirect_stdout(io.StringIO()): main()

    def test_worker_obeys_job_limit_while_draining_several_fast_jobs(self):
        queue=Mock();queue.work_one.return_value=dict(state='done')
        self.run_worker(queue,3)
        self.assertEqual(queue.work_one.call_count,3)
        queue.close.assert_called_once()

    def test_long_job_returns_control_to_collection_before_next_claim(self):
        elapsed=[0]
        queue=Mock()
        def work():
            elapsed[0]+=15
            return dict(state='done')
        queue.work_one.side_effect=work
        bootstrap=Mock();bootstrap.step.return_value=None
        self.run_worker(queue,2,bootstrap=bootstrap,clock=lambda:elapsed[0])
        self.assertEqual(bootstrap.step.call_count,2)
        self.assertEqual(queue.work_one.call_count,2)
