"""Transactional, checksummed migrations packaged with the application."""
import hashlib
from importlib.resources import files
from .database import Database, database_target


def migrate(target=None):
    db = Database(target if target is not None else database_target())
    try:
        if db.dialect != 'postgres':
            raise ValueError('migrate requires a PostgreSQL URL; SQLite initializes automatically')
        applied = []
        with db:
            db.execute('SELECT pg_advisory_xact_lock(1649763001)')
            db.execute('CREATE SCHEMA IF NOT EXISTS deal_finder')
            db.execute('REVOKE ALL ON SCHEMA deal_finder FROM PUBLIC')
            db.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
                name text PRIMARY KEY, checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now())''')
            db.execute('REVOKE ALL ON schema_migrations FROM PUBLIC')
            folder = files('deal_finder').joinpath('migrations')
            for migration in sorted(folder.iterdir(), key=lambda x: x.name):
                if not migration.name.endswith('.sql'):
                    continue
                sql = migration.read_text()
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                old = db.execute('SELECT checksum FROM schema_migrations WHERE name=?', (migration.name,)).fetchone()
                if old:
                    if old[0] != checksum:
                        raise ValueError('Previously applied migration changed: ' + migration.name)
                    continue
                db.executescript(sql)
                db.execute('INSERT INTO schema_migrations(name, checksum) VALUES (?, ?)', (migration.name, checksum))
                applied.append(migration.name)
        return {'status': 'ready', 'backend': 'postgres', 'schema': 'deal_finder', 'applied': applied}
    finally:
        db.close()
