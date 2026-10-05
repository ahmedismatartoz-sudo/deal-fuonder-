import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from test_core import row, NOW
from test_agents import envelope, proof
from deal_finder.queue import Queue
from deal_finder.agents import analyze
from deal_finder.models import Listing

class QueueTests(unittest.TestCase):
    def test_parts_research_envelope_is_preserved_and_not_quarantined(self):
        raw=envelope();raw['parts_research']={'vehicle':{'vehicle_id':'vehicle-99'},'parts':[]}
        receipt=self.queue.submit('parts-handoff',[raw])
        self.assertEqual(receipt['quarantined_at_intake'],0)
        stored=self.queue.db.execute('SELECT payload FROM raw_records WHERE batch_id=?',('parts-handoff',)).fetchone()[0]
        self.assertEqual(self.queue.db.json_decode(stored)['parts_research'],raw['parts_research'])
    def test_attempt_limit_is_a_positive_integer(self):
        for value in (True,'3',0):
            with self.assertRaises(ValueError): self.queue.claim(max_attempts=value)

    def test_photo_conflicts_are_persisted_without_mutating_source_identity(self):
        raw=envelope();raw['listing']['image_urls']=['https://example.com/front.jpg']
        records=[raw]+[dict(listing=row(i),identity_evidence=proof(f'vehicle-{i}')) for i in range(8)]
        receipt=self.queue.submit('photo-persisted',records)
        self.assertEqual(receipt['quarantined_at_intake'],0)
        conflict=dict(status='needs_review', conflicting_fields=[dict(field='model',values=['panda','500'])])
        with patch.dict('os.environ',DEAL_FINDER_PHOTO_IDENTITY_ENABLED='1'):
            with patch('deal_finder.agents.workflow.identify_photos',return_value=conflict):
                result=self.queue.work_one()
        self.assertEqual(result['state'],'done')
        output=self.queue.result(result['job_id'])['run']['outputs']
        self.assertEqual(output['photo_identity']['conflicting_fields'][0]['field'],'model')
        self.assertEqual(output['handoff']['route'],'enrichment')
        self.assertFalse(output['components']['supervisor']['data']['checks']['photo_identity_consistent'])
        self.assertEqual(output['quality']['data']['listing']['model'],'panda')
        self.assertFalse(output['components']['publication']['data']['publishable'])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.temp.name)/'queue.db')
        self.queue=Queue(self.path)
    def tearDown(self):
        self.queue.close();self.temp.cleanup()
    def records(self):
        return [dict(listing=row(i), identity_evidence=proof(f'vehicle-{i}')) for i in range(8)]+[envelope()]
    def test_batch_idempotency_and_conflict(self):
        self.queue.submit('batch',self.records())
        self.assertTrue(self.queue.submit('batch',self.records())['idempotent'])
        self.assertEqual(self.queue.batch('batch')['jobs'],{'pending':9})
        with self.assertRaises(ValueError):self.queue.submit('batch',[envelope()])
    def test_quarantine_preserves_raw_and_does_not_block_good_records(self):
        bad=dict(listing=row(4,price_eur=-1))
        receipt=self.queue.submit('dirty',[dict(listing=row(1)),bad,42])
        self.assertEqual(receipt['quarantined_at_intake'],2)
        self.assertEqual(len(self.queue.listings()),1)
        for _ in range(3):self.queue.work_one()
        out=self.queue.result(2)['run']['outputs']
        self.assertEqual(out['quality']['status'],'quarantined')
        saved=self.queue.db.json_decode(self.queue.db.execute('SELECT payload FROM raw_records WHERE ordinal=1').fetchone()[0])
        self.assertEqual(saved,bad)
    def test_all_snapshots_available_before_first_job(self):
        self.queue.submit('batch',self.records())
        self.queue.work_one()
        market=self.queue.result(1)['run']['outputs']['market']['data']
        self.assertEqual(market['comparable_count'],8)
    def test_restart_and_replay_preserve_runs(self):
        self.queue.submit('batch',self.records());self.queue.work_one()
        self.queue.close();self.queue=Queue(self.path)
        for _ in range(8):self.queue.work_one()
        self.assertEqual(self.queue.batch('batch')['jobs'],{'done':9})
        out=self.queue.result(9)['run']['outputs']
        self.assertTrue(out['validation']['data']['scenario_ready'])
        self.queue.replay_batch('batch')
        self.assertEqual(self.queue.batch('batch')['jobs'],{'done':9,'pending':9})
        self.assertEqual(self.queue.db.execute('SELECT COUNT(*) FROM agent_runs').fetchone()[0],9)
    def test_leases_fence_two_workers_and_recover_crash(self):
        self.queue.submit('one',[envelope()])
        other=Queue(self.path)
        try:
            first=self.queue.claim(lease_seconds=1)
            self.assertIsNone(other.claim())
            future=datetime.now(timezone.utc)+timedelta(seconds=2)
            second=other.claim(at=future)
            self.assertEqual(second['id'],first['id'])
            with self.assertRaises(ValueError):self.queue.finish(first,future,[],{},at=future)
            other.finish(second,future,[],{},at=future)
            self.assertEqual(other.result(1)['state'],'done')
        finally:other.close()
    def test_retry_and_dead_letter(self):
        self.queue.submit('one',[envelope()])
        for _ in range(3):
            with self.queue.db:self.queue.db.execute("UPDATE jobs SET available_at=?",((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
            with patch('deal_finder.queue.analyze',side_effect=RuntimeError('simulated crash')):
                self.queue.work_one()
        self.assertEqual(self.queue.result(1)['state'],'dead')
        self.assertEqual(self.queue.result(1)['attempts'],3)
    def test_final_expired_attempt_is_dead(self):
        self.queue.submit('one',[envelope()]);self.queue.claim(lease_seconds=1,max_attempts=1)
        self.assertIsNone(self.queue.claim(at=datetime.now(timezone.utc)+timedelta(seconds=2),max_attempts=1))
        self.assertEqual(self.queue.result(1)['state'],'dead')
    def test_corrected_specs_supersede_old_listing(self):
        self.queue.submit('old',[dict(listing=row(1))])
        self.queue.submit('new',[dict(listing=row(1,trim='different',observed_at=datetime.now(timezone.utc).isoformat()))])
        found=self.queue.candidates(Listing.parse(row(99)),datetime.now(timezone.utc))
        self.assertEqual(found,[])
    def test_run_inputs_reproduce_outputs(self):
        self.queue.submit('batch',self.records())
        for _ in range(9):self.queue.work_one()
        inputs,as_of,outputs=self.queue.db.execute('SELECT inputs,as_of,outputs FROM agent_runs WHERE job_id=9').fetchone()
        context=self.queue.db.json_decode(inputs)
        proofs={tuple(x['key']):x['proof'] for x in context['identity_evidence']}
        actual=analyze(context['raw'],[Listing.parse(x) for x in context['candidates']],datetime.fromisoformat(as_of),identity_evidence=proofs)
        self.assertEqual(actual,self.queue.db.json_decode(outputs))
    def test_missing_identity_evidence_excluded_from_market(self):
        self.queue.submit('batch',[dict(listing=row(i)) for i in range(8)]+[envelope()])
        for _ in range(9):self.queue.work_one()
        market=self.queue.result(9)['run']['outputs']['market']
        self.assertEqual(market['status'],'blocked')
        self.assertEqual(market['data']['comparable_count'],0)
