import io
import os
import threading
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from deal_finder.worker import main

class PauseTests(unittest.TestCase):
    def test_paused_scheduled_scan_never_opens_a_database(self):
        for command in ('daily-cycle','market-scan'):
            out=io.StringIO()
            with patch.dict(os.environ,DEAL_FINDER_WORKER_PAUSED='1'),patch('sys.argv',['worker',command]):
                with patch('deal_finder.worker.Queue',side_effect=AssertionError('No DB access')),redirect_stdout(out):main()
            self.assertIn('"worker": "paused"',out.getvalue())
    def test_paused_run_stays_idle_without_starting_collectors_or_analysis(self):
        stop=threading.Event();stop.set()
        with patch.dict(os.environ,DEAL_FINDER_WORKER_PAUSED='1'),patch('sys.argv',['worker','run']):
            with patch('threading.Event',return_value=stop),patch('deal_finder.worker.Queue',side_effect=AssertionError('No DB access')):
                with redirect_stdout(io.StringIO()):main()
