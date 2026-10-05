import unittest
from unittest.mock import patch
from deal_finder.database import Database, database_target, validate_postgres_url
from deal_finder.serve import validate_runtime

class ConfigurationTests(unittest.TestCase):
    def test_database_selection_and_no_silent_fallback(self):
        with patch.dict('os.environ',{'DEAL_FINDER_DB':'local.db'},clear=True):
            self.assertEqual(database_target(),'local.db')
        with patch.dict('os.environ',{'DEAL_FINDER_DATABASE_URL':''},clear=True):
            with self.assertRaises(ValueError):database_target()
    def test_remote_tls_and_session_pooler(self):
        self.assertEqual(validate_postgres_url('postgresql://user:example@host.example/database'),'require')
        with self.assertRaises(ValueError):validate_postgres_url('postgresql://user:example@host.example/database?sslmode=disable')
        with self.assertRaises(ValueError):validate_postgres_url('postgresql://user:example@aws-0-eu.pooler.supabase.com:6543/postgres')
        self.assertEqual(validate_postgres_url('postgresql://test:test@127.0.0.1/test?sslmode=disable'),'disable')
    def test_production_requires_database_and_token(self):
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaises(RuntimeError):validate_runtime()
        with patch.dict('os.environ',{'DEAL_FINDER_DATABASE_URL':'postgresql://u:example@host.example/db','DEAL_FINDER_API_TOKEN':'short'},clear=True):
            with self.assertRaises(RuntimeError):validate_runtime()
    def test_sqlite_savepoint_does_not_commit_partial_record(self):
        db=Database(':memory:');db.execute('CREATE TABLE sample(id INTEGER PRIMARY KEY)')
        db.begin()
        with self.assertRaises(ValueError):
            with db.savepoint():
                db.execute('INSERT INTO sample VALUES (1)')
                raise ValueError('record conflict')
        db.execute('INSERT INTO sample VALUES (2)');db.commit()
        self.assertEqual(list(db.execute('SELECT id FROM sample')),[(2,)])
        db.close()
