import hashlib
import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from test_core import NOW
from test_archive import page
from test_market import complete
from deal_finder.archive import Archive
from deal_finder.photo_archive import PhotoArchive,approved_url,download,MAX_PHOTO_BYTES
from deal_finder.api import app

JPEG=b'\xff\xd8\xff\xe0retained-photo-test\xff\xd9'
URL_IMAGE='https://prod.pictures.autoscout24.net/one.jpg'

class PhotoArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=os.path.join(self.tmp.name,'photos.db')
        self.archive=Archive(self.path);self.photos=PhotoArchive(self.archive.db)
        self.record=complete('one',image_urls=[URL_IMAGE])
        self.archive.ingest(page([self.record]),as_of=NOW)
    def tearDown(self):self.archive.close();self.tmp.cleanup()
    def retain(self,**kwargs):
        return self.photos.retain('export','one',self.record['observed_at'],0,URL_IMAGE,as_of=NOW,**kwargs)
    def test_source_bound_bytes_survive_source_loss_and_are_deduplicated(self):
        fetch=lambda u:(JPEG,'image/jpeg')
        self.assertTrue(self.retain(fetch=fetch)['saved'])
        sha=hashlib.sha256(JPEG).hexdigest()
        self.assertEqual(self.archive.db.execute('SELECT content FROM photo_assets WHERE sha=?',(sha,)).fetchone()[0],JPEG)
        self.assertEqual(self.retain(fetch=lambda u:(_ for _ in ()).throw(AssertionError('No redownload')))['status'],'already_archived')
        self.assertEqual(self.photos.manifest('export','one',self.record['observed_at'])[0]['retained_url'],'/archive/photos/'+sha)
        with self.assertRaises(ValueError):self.photos.retain('export','one',self.record['observed_at'],0,'https://prod.pictures.autoscout24.net/forged.jpg',fetch=fetch)
    def test_unsafe_hosts_html_oversized_and_rejected_downloads_never_count_as_saved(self):
        for u in ['http://prod.pictures.autoscout24.net/one.jpg','https://localhost/test','https://evil.autoscout24.net.attacker.example/x','https://127.0.0.1/test','https://example.com/photo.jpg']:
            with self.assertRaises(ValueError):approved_url(u)
        result=self.retain(fetch=lambda u:(b'<html>sign in</html>','image/jpeg'))
        self.assertFalse(result['saved']);self.assertEqual(self.photos.manifest('export','one',self.record['observed_at']),[])
        self.assertEqual(self.retain(fetch=lambda u:(JPEG,'image/jpeg'))['status'],'already_unavailable')
    def test_byte_budget_and_cover_priority(self):
        other=complete('two',image_urls=[URL_IMAGE.replace('one','two')])
        self.archive.ingest(page([other],run='second'),as_of=NOW)
        report=dict(candidates=[dict(source='export',source_id=x['source_id'],observed_at=x['observed_at']) for x in [self.record,other]])
        with patch('deal_finder.photo_archive.MAX_STORED_BYTES',len(JPEG)):
            result=self.photos.step(report,fetch=lambda u:(JPEG,'image/jpeg'))
            # Identical bytes consume capacity only once.
            self.assertTrue(result['storage_limit'])
            self.assertEqual(self.archive.db.execute('SELECT count(*) FROM photo_assets').fetchone()[0],1)
    def test_authenticated_endpoint_returns_saved_bytes_without_fetching_source(self):
        self.retain(fetch=lambda u:(JPEG,'image/jpeg'))
        sha=hashlib.sha256(JPEG).hexdigest()
        with patch.dict(os.environ,DEAL_FINDER_DB=self.path,DEAL_FINDER_API_TOKEN='test-token'):
            client=TestClient(app)
            self.assertEqual(client.get('/archive/photos/'+sha).status_code,401)
            response=client.get('/archive/photos/'+sha,headers={'Authorization':'Bearer test-token'})
            self.assertEqual(response.content,JPEG)
            self.assertEqual(response.headers['content-type'],'image/jpeg')
            self.assertEqual(response.headers['x-content-type-options'],'nosniff')

    def test_production_analysis_progresses_while_collection_waits(self):
        import io
        import threading
        from contextlib import redirect_stdout
        from unittest.mock import Mock
        from deal_finder.worker import main
        progressed=threading.Event()
        memory=Mock();memory.ready=True
        memory.step.side_effect=lambda run: progressed.set() or dict(price_memory='building')
        replay=Mock()
        replay.step.side_effect=lambda: (None if progressed.wait(timeout=2) else (_ for _ in ()).throw(AssertionError('Analysis did not progress independently')))
        queue=Mock();queue.work_one.return_value=dict(state='done')
        env=dict(DEAL_FINDER_MODE='production',DEAL_FINDER_FIRST_ARCHIVE_TEST='',DEAL_FINDER_AGENT_SCHEDULER_ENABLED='',
                 DEAL_FINDER_BRIGHTDATA_CAMPAIGN='',DEAL_FINDER_BRIGHTDATA_CONFIG='')
        with patch.dict(os.environ,env),patch('sys.argv',['worker','--db','postgresql://localhost/disposable','run','--max-jobs','1']):
            with patch('deal_finder.worker.Queue',return_value=queue),patch('deal_finder.bootstrap.bootstrap_config',return_value=None):
                with patch('deal_finder.price_memory.BackgroundPriceMemory',return_value=memory),patch('deal_finder.photo_archive.BackgroundPhotoArchive',return_value=Mock(step=Mock(return_value=None))):
                    with patch('deal_finder.brightdata.BackgroundArchiveRevalidation',return_value=replay),redirect_stdout(io.StringIO()):
                        main()
        self.assertTrue(progressed.is_set())
        self.assertEqual(queue.work_one.call_count,1)


class OpportunityPhotoTests(unittest.TestCase):
    setUp = PhotoArchiveTests.setUp
    tearDown = PhotoArchiveTests.tearDown
    def test_background_uses_actual_versioned_report_not_base_run_id(self):
        from deal_finder.price_memory import PriceMemory, SCREENING_VERSION
        from deal_finder.photo_archive import BackgroundPhotoArchive
        from deal_finder.archive import canonical
        PriceMemory(self.archive.db)
        report=dict(screening_version=SCREENING_VERSION,candidates=[dict(source='export',source_id='one',observed_at=self.record['observed_at'])])
        with self.archive.db:
            self.archive.db.execute('INSERT INTO price_test_reports VALUES (?,?,?)',('archive-continuous-auto-day-'+SCREENING_VERSION,NOW.isoformat(),canonical(report)))
        with patch('deal_finder.photo_archive.download',return_value=(JPEG,'image/jpeg')):
            # The source URL is intentionally unapproved in an ordinary record;
            # setUp uses the permitted marketplace CDN.
            result=BackgroundPhotoArchive(self.path).step('archive-continuous')
        self.assertEqual(result['saved'],1)
    def test_no_opportunity_means_no_photo_download(self):
        from deal_finder.price_memory import PriceMemory, SCREENING_VERSION
        from deal_finder.photo_archive import BackgroundPhotoArchive
        from deal_finder.archive import canonical
        PriceMemory(self.archive.db)
        with self.archive.db:
            self.archive.db.execute('INSERT INTO price_test_reports VALUES (?,?,?)',('test',NOW.isoformat(),canonical(dict(screening_version=SCREENING_VERSION,candidates=[]))))
        with patch('deal_finder.photo_archive.download') as fetch:
            self.assertIsNone(BackgroundPhotoArchive(self.path).step('test'))
            fetch.assert_not_called()

    def test_large_discovery_rotates_bounded_photo_groups(self):
        from deal_finder.price_memory import PriceMemory,SCREENING_VERSION
        from deal_finder.photo_archive import BackgroundPhotoArchive
        from deal_finder.archive import canonical
        PriceMemory(self.archive.db)
        candidates=[dict(source='export',source_id=str(i),observed_at=NOW.isoformat()) for i in range(45)]
        with self.archive.db:
            self.archive.db.execute('INSERT INTO price_test_reports VALUES (?,?,?)',
                ('large',NOW.isoformat(),canonical(dict(screening_version=SCREENING_VERSION,candidates=candidates))))
        worker=BackgroundPhotoArchive(self.path)
        with patch.dict(os.environ,DEAL_FINDER_PHOTO_OPPORTUNITY_REVIEW_ENABLED='0'),patch('deal_finder.photo_archive.PhotoArchive.step',return_value=dict(photo_archive='idle')) as step:
            worker.step('large');worker.last_poll=None
            worker.step('large')
        first=step.call_args_list[0].args[0]['candidates'];second=step.call_args_list[1].args[0]['candidates']
        self.assertEqual(len(first),20);self.assertEqual(len(second),20)
        self.assertFalse({p['source_id'] for p in first}&{p['source_id'] for p in second})

    def test_apparent_opportunity_alone_gets_photos_and_one_source_bound_review(self):
        from datetime import datetime,timezone
        from deal_finder.price_memory import PriceMemory
        from deal_finder.photo_archive import BackgroundPhotoArchive
        now=datetime.now(timezone.utc)
        opportunity=complete('opportunity',observed_at=now.isoformat(),image_urls=[URL_IMAGE])
        opportunity['payload']['collection_price_screen']=dict(status='apparent_opportunity')
        ordinary=complete('ordinary',observed_at=now.isoformat(),image_urls=[URL_IMAGE.replace('one','ordinary')])
        ordinary['payload']['collection_price_screen']=dict(status='not_apparent_opportunity')
        self.archive.ingest(page([opportunity,ordinary],run_id='photo-test'),as_of=now)
        memory=PriceMemory(self.archive.db);memory.sync(now)
        with patch.dict(os.environ,DEAL_FINDER_PHOTO_OPPORTUNITY_REVIEW_ENABLED='1'),patch('deal_finder.photo_archive.download',return_value=(JPEG,'image/jpeg')) as fetch:
            first=BackgroundPhotoArchive(self.path).step('archive-continuous')
            second=BackgroundPhotoArchive(self.path).step('archive-continuous')
        self.assertEqual(first['saved'],1);self.assertEqual(first['queued_photo_reviews'],1)
        self.assertEqual(fetch.call_count,1);self.assertIsNone(second)
        refs=self.archive.db.execute('SELECT source_id FROM photo_references').fetchall()
        self.assertEqual(refs,[('opportunity',)])
