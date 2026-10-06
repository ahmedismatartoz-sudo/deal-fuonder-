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
    def test_partial_projection_does_not_create_complete_test(self):
        self.archive.ingest(page([complete('new')]),as_of=NOW)
        self.assertEqual(self.memory.first_test('pending',NOW)['status'],'waiting_for_price_memory')
        self.assertEqual(self.archive.db.execute('SELECT count(*) FROM price_test_reports').fetchone()[0],0)
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
