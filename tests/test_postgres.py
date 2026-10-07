"""Integration tests only target an explicit disposable loopback test database."""
import os
import json
import unittest
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import test_queue
from test_agents import envelope
from test_core import row
from deal_finder.database import Database
from deal_finder.migrate import migrate
from deal_finder.queue import Queue

URL=os.getenv('DEAL_FINDER_TEST_DATABASE_URL')

@unittest.skipUnless(URL, 'Disposable PostgreSQL test database not configured')
class PostgresQueueTests(test_queue.QueueTests):
    def test_facebook_archive_screen_uses_postgres_and_persists_separate_result(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        from deal_finder.archive import Archive
        from deal_finder.price_memory import PriceMemory
        from deal_finder.facebook_opportunities import FacebookScreening
        from test_facebook_opportunities import car, peers
        from test_archive import page
        archive=Archive(URL)
        try:
            target=car(description='80000 km benzina')
            def record(p):
                return dict(source_id=p['source_id'],observed_at=p['observed_at'],url=p['url'],active=p['active'],payload=p)
            archive.ingest(page([record(target)],source='facebook_marketplace',run='facebook'))
            archive.ingest(page([record(p) for p in peers()],source='export',run='prices'))
            memory=PriceMemory(archive.db)
            now=datetime.now(timezone.utc)
            while memory.sync(now,recover_identity=False):pass
        finally:
            archive.close()
        with patch.dict(os.environ,{'DEAL_FINDER_FACEBOOK_RESEARCH_LIMIT':'0'}):
            result=FacebookScreening(URL).step()
            self.assertEqual(result['facebook_screening'],'completed')
            self.assertEqual(result['facebook_ads_examined'],1)
            self.assertEqual(result['research_candidates'],1)
            saved=self.queue.db.execute('SELECT payload FROM price_test_reports WHERE run_id=?',(result['run_id'],)).fetchone()[0]
            self.assertEqual(saved['opportunities'],[])
            self.assertEqual(saved['net_margin_policy']['version'],'tiered-net-margin-v2')
            self.assertEqual(FacebookScreening(URL).step()['facebook_screening'],'already_completed')

    def setUp(self):
        parsed=urlparse(URL)
        if parsed.hostname not in ('127.0.0.1','localhost') or parsed.path != '/deal_finder_test':
            raise RuntimeError('PostgreSQL tests require a disposable loopback deal_finder_test database')
        db=Database(URL)
        db.execute('DROP SCHEMA IF EXISTS deal_finder CASCADE')
        db.close()
        migrate(URL)
        self.path=URL
        self.queue=Queue(URL)
    def tearDown(self):
        self.queue.close()
    def test_extended_source_recovery_under_backend_role_and_damage_mix(self):
        from deal_finder.archive import Archive
        from deal_finder.price_memory import PriceMemory
        from test_archive import page
        from test_market import complete
        from test_core import NOW
        archive=Archive(URL)
        try:
            archive.db.execute('SET ROLE deal_finder_backend')
            record=complete('extended',price_eur=2000,version_text='Panda III 1.2 Easy 69cv',trim='Easy')
            record['payload'].pop('generation')
            record['payload']['original']=dict(vehicle=dict(rawPowerInHp=69,rawPowerInKw=51,
                rawCylinderCapacity=1242,transmissionType='Cambio manuale',driveTrain='Anteriore',bodyType='City car'),
                seller=dict(type='PrivateSeller'),unused_large='x'*100000)
            archive.ingest(page([record],source='autoscout24'),as_of=NOW)
            memory=PriceMemory(archive.db)
            memory.sync(NOW)
            current=next(memory.current(NOW))
            self.assertEqual(current['power_kw'],51);self.assertEqual(current['drivetrain'],'fwd')
            self.assertEqual(current['body_type'],'city_car');self.assertEqual(current['generation'],'iii')
            self.assertFalse(current['identity_dossier']['conflicts'])
            self.assertNotIn('unused_large',str(current))
            report=memory.first_test('identity-role',NOW,profile='opportunities')
            self.assertEqual(report['identity_quality']['fields']['power_kw']['recovered_normalization_gaps'],1)
            self.assertTrue(report['damage_mix']['enforced'])
        finally:archive.close()

    def test_migrations_idempotent(self):
        self.assertEqual(migrate(URL)['applied'],[])
        self.assertTrue(self.queue.db.schema_ready())
    def test_retained_photo_bytes_and_provenance_under_private_role(self):
        from deal_finder.archive import Archive
        from deal_finder.photo_archive import PhotoArchive
        from test_market import complete
        from test_archive import page
        from test_core import NOW
        from test_photo_archive import JPEG, URL_IMAGE
        archive=Archive(URL)
        archive.db.execute('SET ROLE deal_finder_backend')
        try:
            record=complete('photo',image_urls=[URL_IMAGE])
            archive.ingest(page([record]),as_of=NOW)
            photos=PhotoArchive(archive.db)
            result=photos.retain('export','photo',record['observed_at'],0,URL_IMAGE,fetch=lambda u:(JPEG,'image/jpeg'),as_of=NOW)
            self.assertTrue(result['saved'])
            self.assertEqual(bytes(archive.db.execute('SELECT content FROM photo_assets').fetchone()[0]),JPEG)
            self.assertTrue(photos.manifest('export','photo',record['observed_at'])[0]['archived'])
        finally: archive.close()
    def test_price_memory_backend_bulk_insert_and_priority_report(self):
        from deal_finder.archive import Archive
        from deal_finder.price_memory import PriceMemory
        from test_market import complete
        from test_archive import page
        from test_core import NOW
        archive=Archive(URL)
        archive.db.execute('SET ROLE deal_finder_backend')
        try:
            record=complete('memory',make='  FIAT   AUTO  ',model=' Panda ',
                            damage_indicators=['unverified'],description='original description')
            record['payload']['original']={'large':'x'*500000}
            record['payload']['image_urls']=['https://example.com/image.jpg']*100
            archive.ingest(page([record]),as_of=NOW)
            memory=PriceMemory(archive.db)
            self.assertEqual(memory.sync(NOW),1)
            self.assertEqual(memory.sync(NOW),0)
            current=list(memory.current(NOW))
            self.assertEqual(len(current),1)
            self.assertEqual(current[0]['make'],'fiat auto')
            self.assertEqual(current[0]['model'],'panda')
            self.assertEqual(current[0]['damage_indicators'],['unverified'])
            self.assertEqual(current[0]['description'],'original description')
            self.assertIsInstance(current[0]['year'],int)
            self.assertNotIn('original',current[0])
            self.assertNotIn('image_urls',current[0])
            report=memory.first_test('postgres-test',NOW)
            self.assertEqual(report['approved_buys'],0)
            self.assertEqual(memory.first_test('postgres-test',NOW),report)
        finally: archive.close()
    def test_source_scalar_recovery_remains_bounded_under_backend_role(self):
        from deal_finder.archive import Archive
        from deal_finder.price_memory import PriceMemory
        from test_market import complete
        from test_archive import page
        from test_core import NOW
        archive=Archive(URL)
        try:
            archive.db.execute('SET ROLE deal_finder_backend')
            record=complete('retained')
            record['payload'].pop('seller_type')
            record['payload']['original']=dict(seller=dict(type='PrivateSeller'),
                vehicle=dict(rawPowerInHp=69,rawCylinderCapacity=1242),unused_large='x'*500000)
            archive.ingest(page([record],source='autoscout24'),as_of=NOW)
            memory=PriceMemory(archive.db)
            self.assertEqual(memory.sync(NOW),1)
            value=next(memory.current(NOW))
            self.assertEqual(value['power_hp'],69)
            self.assertEqual(value['seller_type'],'private')
            self.assertEqual(value['displacement_cc'],1242)
            self.assertNotIn('unused_large',str(value))
            self.assertEqual(memory.sync(NOW),0)
            self.assertEqual(archive.db.execute('SELECT count(*) FROM identity_source_cache').fetchone()[0],1)
            self.assertFalse(archive.db.execute("SELECT has_table_privilege('deal_finder_backend','deal_finder.identity_source_cache','UPDATE')").fetchone()[0])
        finally:archive.close()

    def test_price_projection_skips_busy_projector_and_finds_late_arriving_keys(self):
        from deal_finder.archive import Archive
        from deal_finder.price_memory import PriceMemory
        from test_market import complete
        from test_archive import page
        from test_core import NOW
        archive,other=Archive(URL),Database(URL)
        try:
            archive.db.execute('SET ROLE deal_finder_backend')
            archive.ingest(page([complete('z-last')]),as_of=NOW)
            memory=PriceMemory(archive.db)
            with other:
                other.execute('SELECT pg_advisory_xact_lock(1649763002)')
                self.assertEqual(memory.sync(NOW),0)
                self.assertTrue(memory.pending(NOW))
            self.assertEqual(memory.sync(NOW),1)
            cursor=memory.next_cursor
            archive.ingest(page([complete('a-late')],run='late-arrival'),as_of=NOW)
            self.assertEqual(memory.sync(NOW,after=cursor),0)
            self.assertIsNone(memory.next_cursor)
            self.assertTrue(memory.pending(NOW))
            self.assertEqual(memory.sync(NOW),1)
            self.assertFalse(memory.pending(NOW))
            self.assertEqual({p['source_id'] for p in memory.current(NOW)},{'z-last','a-late'})
        finally:
            other.close();archive.close()
    def test_regional_facebook_replay_under_private_backend_role(self):
        from unittest.mock import patch
        from deal_finder.archive import Archive
        from deal_finder.brightdata import cycle, BackgroundArchiveRevalidation
        from test_brightdata import FakeClient, config, car
        archive = Archive(URL)
        archive.db.execute('SET ROLE deal_finder_backend')
        client = FakeClient(); client.state = 'ready'
        client.rows = [dict(car(), location='Monza, Monza e Brianza')]
        try:
            with patch('deal_finder.brightdata.event', side_effect=ValueError('old geography')):
                cycle(config(), archive, client=client, free_confirmed=True)
            def backend_archive(path):
                instance = Archive(path)
                instance.db.execute('SET ROLE deal_finder_backend')
                return instance
            with patch('deal_finder.archive.Archive', side_effect=backend_archive):
                replay = BackgroundArchiveRevalidation(URL)
                self.assertEqual(replay.step()['new_unique'], 1)
                self.assertEqual(replay.step()['brightdata'], 'archive_revalidation_complete')
            self.assertEqual(archive.history('facebook_marketplace', '123')[0]['payload']['province'], 'MB')
            self.assertEqual(client.starts, 1)
        finally:
            archive.close()
    def test_collector_lock_across_connections_and_latest_quality_with_rls(self):
        from deal_finder.archive import Archive
        from deal_finder.autoscout24 import collect, CollectionBusy
        from test_autoscout24 import config, FakeClient, item
        archive, other = Archive(URL), Database(URL)
        key = 'deal-finder:autoscout24-public'
        try:
            archive.db.execute('SET ROLE deal_finder_backend')
            other.execute('SELECT pg_advisory_lock(hashtextextended(?, 0))', (key,))
            client = FakeClient([[item()]])
            with self.assertRaises(CollectionBusy):
                collect(config(), archive, run_id='locked', client=client)
            self.assertEqual(client.calls, [])
            other.execute('SELECT pg_advisory_unlock(hashtextextended(?, 0))', (key,))
            collect(config(), archive, run_id='locked', client=client)
            self.assertEqual(archive.quality('autoscout24')['listings'], 1)
            self.assertEqual(archive.quality('autoscout24')['photo_links'], 1)
        finally:
            other.close()
            archive.close()
    def test_private_backend_role_can_process_but_not_rewrite_history(self):
        import psycopg
        db = self.queue.db
        db.execute('SET ROLE deal_finder_backend')
        try:
            receipt = self.queue.submit('scoped-backend', [envelope()])
            self.assertEqual(receipt['record_count'], 1)
            self.assertEqual(self.queue.work_one()['state'], 'done')
            self.assertEqual(db.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 8)
            for statement in (
                'UPDATE snapshots SET payload=payload',
                'DELETE FROM snapshots',
                "INSERT INTO schema_migrations(name,checksum) VALUES ('untrusted','x')",
                'CREATE TABLE deal_finder.untrusted(id integer)',
            ):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    db.execute(statement)
        finally:
            db.execute('RESET ROLE')
    def test_all_private_tables_have_rls(self):
        rows = self.queue.db.execute("""SELECT c.relname, c.relrowsecurity
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='deal_finder' AND c.relkind='r'""").fetchall()
        self.assertEqual(len(rows), 20)
        self.assertTrue(all(enabled for _, enabled in rows))
        for table, _ in rows:
            self.assertFalse(self.queue.db.execute(
                'SELECT has_table_privilege(?, ?, ?)',
                ('deal_finder_backend', 'deal_finder.' + table, 'DELETE')).fetchone()[0])
    def test_plate_lookup_quota_cache_and_private_permissions(self):
        from deal_finder.plate_lookup import PlateLookup
        from test_plate_lookup import response
        from test_core import NOW
        from unittest.mock import patch
        from datetime import timedelta
        import psycopg
        calls = []
        def transport(token, plate):
            calls.append(plate)
            return response(plate)
        lookup = PlateLookup(URL, transport)
        lookup.db.execute('SET ROLE deal_finder_backend')
        try:
            with patch.dict(os.environ, DEAL_FINDER_TUTTOTARGHE_TOKEN='synthetic-test-token',
                            DEAL_FINDER_PLATE_FREE_PLAN_CONFIRMED='tuttotarghe-direct-10-per-day'):
                for i in range(9):
                    lookup.lookup(f'AB{i:03d}CD', as_of=NOW+timedelta(seconds=i*10))
                self.assertTrue(lookup.lookup('AB000CD', as_of=NOW)['cached'])
                self.assertEqual(lookup.lookup('AB999CD', as_of=NOW+timedelta(seconds=100))['status'], 'free_quota_exhausted')
            self.assertEqual(len(calls), 9)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                lookup.db.execute('DELETE FROM plate_lookup_attempts')
        finally:
            lookup.close()

    def test_parallel_claims_are_distinct(self):
        self.queue.submit('parallel',[envelope() for _ in range(10)])
        barrier=Barrier(4)
        def claim(_):
            queue=Queue(URL)
            try:
                barrier.wait(timeout=10)
                return queue.claim()['id']
            finally:queue.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids=list(pool.map(claim,range(4)))
        self.assertEqual(len(set(ids)),4)
    def test_parallel_duplicate_batch_is_idempotent(self):
        barrier=Barrier(2)
        def submit(_):
            queue=Queue(URL)
            try:
                barrier.wait(timeout=10)
                return queue.submit('same',[envelope()])
            finally:queue.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts=list(pool.map(submit,range(2)))
        self.assertEqual(sorted(x['idempotent'] for x in receipts),[False,True])
        self.assertEqual(self.queue.batch('same')['jobs'],{'pending':1})
    def test_conflicting_proof_rolls_back_just_one_record(self):
        first=envelope();self.queue.submit('first',[first])
        bad=envelope();bad['identity_evidence']['verified_by']='different-verifier'
        result=self.queue.submit('conflict',[bad,dict(listing=row(1))])
        self.assertEqual(result['quarantined_at_intake'],1)
        self.assertEqual(len(self.queue.listings()),2)
    def test_jsonb_and_timestamps_reproduce_inputs(self):
        self.queue.submit('typed',[envelope()]);self.queue.work_one()
        result=self.queue.result(1)
        self.assertIsInstance(result['run']['as_of'],str)
        self.assertIsInstance(result['run']['outputs'],dict)
        # JSONB input containing percent signs remains bound data, not SQL syntax.
        sample=envelope();sample['listing']['source_id']='with%sign'
        self.queue.submit('percent',[sample])
        self.assertEqual(self.queue.batch('percent')['record_count'],1)
    def test_api_and_worker_share_postgres(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from deal_finder.api import app
        token='postgres-api-test-token-with-32-characters'
        with patch.dict(os.environ,{'DEAL_FINDER_DATABASE_URL':URL,'DEAL_FINDER_API_TOKEN':token}):
            with TestClient(app) as client:
                headers={'authorization':'Bearer '+token}
                received=client.post('/batches',json={'batch_id':'http','records':[envelope()]},headers=headers)
                self.assertEqual(received.status_code,202)
                processed=self.queue.work_one()
                self.assertEqual(processed['state'],'done')
                result=client.get('/jobs/1',headers=headers)
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['state'],'done')


import test_archive

@unittest.skipUnless(URL, 'Disposable PostgreSQL test database not configured')
class PostgresArchiveTests(test_archive.ArchiveTests):
    def setUp(self):
        parsed=urlparse(URL)
        if parsed.hostname not in ('127.0.0.1','localhost') or parsed.path != '/deal_finder_test':
            raise RuntimeError('Archive tests require disposable loopback PostgreSQL')
        db=Database(URL)
        db.execute('DROP SCHEMA IF EXISTS deal_finder CASCADE')
        db.close()
        migrate(URL)
        from deal_finder.archive import Archive
        self.archive=Archive(URL)

    def test_native_autoscout_checkpoint_and_new_only_on_postgres(self):
        from deal_finder.autoscout24 import collect
        from test_autoscout24 import FakeClient, config, item
        provider = FakeClient([[item('car1')], [item('car2')]])
        first = collect(config(), self.archive, run_id='pg-native', mode='initial', max_pages=1, client=provider)
        self.assertFalse(first['complete'])
        resumed = collect(config(), self.archive, run_id='pg-native', mode='initial', client=provider)
        self.assertTrue(resumed['complete'])
        self.assertEqual(resumed['accepted'], 2)
        daily = FakeClient([[item('car1'), item('new')]])
        result = collect(config(), self.archive, run_id='pg-next-day', client=daily)
        self.assertEqual(result['accepted'], 1)
        self.assertEqual([kind for _, kind in daily.calls], ['search', 'listing'])
        self.assertEqual(len(self.archive.search()['items']), 3)
        self.assertEqual(self.archive.history('autoscout24', 'car1')[0]['payload']['price_eur'], 8000)

    def test_scoped_backend_can_archive_but_not_rewrite_events(self):
        import psycopg
        self.archive.db.execute('SET ROLE deal_finder_backend')
        try:
            self.ingest(test_archive.page([test_archive.event(), {'invalid':True}]))
            self.assertEqual(len(self.archive.search()['items']), 1)
            self.assertEqual(self.archive.run_status('export','first')['quarantined'], 1)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.archive.db.execute('UPDATE listing_events SET payload=payload')
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.archive.db.execute('DELETE FROM collection_pages')
        finally:
            self.archive.db.execute('RESET ROLE')


import test_market
import test_agent_readiness

@unittest.skipUnless(URL, 'Disposable PostgreSQL test database not configured')
class PostgresAgentReadinessTests(test_agent_readiness.RuntimeTests):
    def setUp(self):
        parsed=urlparse(URL)
        if parsed.hostname not in ('127.0.0.1','localhost') or parsed.path != '/deal_finder_test':
            raise RuntimeError('PostgreSQL tests require a disposable loopback deal_finder_test database')
        db=Database(URL)
        db.execute('DROP SCHEMA IF EXISTS deal_finder CASCADE')
        db.close()
        migrate(URL)
        from deal_finder.market import Market
        self.path=URL
        self.market,self.queue=Market(URL),Queue(URL)

    def tearDown(self):
        self.queue.close()
        self.market.close()

    def test_private_backend_can_organize_enrichment(self):
        self.market.db.execute('SET ROLE deal_finder_backend')
        self.queue.db.execute('SET ROLE deal_finder_backend')
        try:
            self.test_incomplete_ads_are_organized_without_paid_calls_or_attestations()
        finally:
            self.market.db.execute('RESET ROLE')
            self.queue.db.execute('RESET ROLE')

@unittest.skipUnless(URL, 'Disposable PostgreSQL test database not configured')
class PostgresMarketTests(test_market.MarketTests):
    def setUp(self):
        parsed=urlparse(URL)
        if parsed.hostname not in ('127.0.0.1','localhost') or parsed.path != '/deal_finder_test':
            raise RuntimeError('Market tests require disposable loopback PostgreSQL')
        db=Database(URL)
        db.execute('DROP SCHEMA IF EXISTS deal_finder CASCADE')
        db.close()
        migrate(URL)
        from deal_finder.market import Market
        self.path=URL
        self.market=Market(URL)
        self.queue=Queue(URL)
    def tearDown(self):
        self.queue.close()
        self.market.close()

    def test_backend_can_screen_but_not_rewrite_reviews(self):
        import psycopg
        self.market.db.execute('SET ROLE deal_finder_backend')
        self.queue.db.execute('SET ROLE deal_finder_backend')
        try:
            self.base()
            self.ingest([test_market.complete('cheap',price_eur=7000)],run='cheap')
            self.assertEqual(self.scan()['queued'],1)
            self.assertEqual(self.market.candidates(as_of=test_market.NOW)['count'],1)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.market.db.execute('UPDATE market_reviews SET payload=payload')
        finally:
            self.market.db.execute('RESET ROLE')
            self.queue.db.execute('RESET ROLE')
