import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_agents import envelope
try:
    from fastapi.testclient import TestClient
    from deal_finder.api import app
except ImportError:
    TestClient = None

@unittest.skipIf(TestClient is None, 'FastAPI/httpx unavailable locally; required in CI')
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'DEAL_FINDER_DB':str(Path(self.temp.name)/'api.db')})
        self.env.start();self.client=TestClient(app)
    def tearDown(self):
        self.client.close();self.env.stop();self.temp.cleanup()
    def test_registry_and_batch_routes(self):
        self.assertEqual(self.client.get('/agents').status_code,200)
        response=self.client.post('/batches',json={'batch_id':'api','records':[envelope()]})
        self.assertEqual(response.status_code,202)
        self.assertEqual(self.client.get('/batches/api').json()['jobs'],{'pending':1})
        self.assertEqual(self.client.get('/batches/api/jobs').json()['jobs'][0]['job_id'],1)
        self.assertEqual(self.client.get('/jobs/1').status_code,200)
        self.assertEqual(self.client.post('/batches/api/replay').status_code,202)
    def test_bad_input_and_missing_entities(self):
        self.assertEqual(self.client.post('/batches',json={'batch_id':'x','records':[]}).status_code,422)
        self.assertEqual(self.client.get('/jobs/999').status_code,404)
        self.assertEqual(self.client.get('/batches/missing').status_code,404)
        self.assertEqual(self.client.get('/batches/missing/jobs?limit=101').status_code,422)
    def test_evaluation_persists_report_without_enabling_forecasts(self):
        response=self.client.post('/evaluations',json={'model_version':'m1','training_vehicle_ids':[],'records':[]})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['result']['status'],'blocked')
        self.assertFalse(response.json()['result']['data']['forecast_release_approved'])
    def test_bearer_authentication(self):
        with patch.dict(os.environ,{'DEAL_FINDER_API_TOKEN':'unit-test-token-with-at-least-32-characters'}):
            self.assertEqual(self.client.get('/health').status_code,200)
            self.assertEqual(self.client.get('/agents').status_code,401)
            self.assertEqual(self.client.post('/batches',json={'batch_id':'private','records':[envelope()]}).status_code,401)
            self.assertEqual(self.client.get('/agents',headers={'authorization':'Bearer unit-test-token-with-at-least-32-characters'}).status_code,200)
