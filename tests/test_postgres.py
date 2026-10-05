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
    def test_migrations_idempotent(self):
        self.assertEqual(migrate(URL)['applied'],[])
        self.assertTrue(self.queue.db.schema_ready())
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
            self.assertEqual(db.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 4)
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
        self.assertEqual(len(rows), 13)
        self.assertTrue(all(enabled for _, enabled in rows))
        for table, _ in rows:
            self.assertFalse(self.queue.db.execute(
                'SELECT has_table_privilege(?, ?, ?)',
                ('deal_finder_backend', 'deal_finder.' + table, 'DELETE')).fetchone()[0])
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
