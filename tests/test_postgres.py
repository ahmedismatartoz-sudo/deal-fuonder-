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
