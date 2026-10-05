"""Small storage boundary for SQLite development and PostgreSQL deployment.
All query parameters remain bound; PostgreSQL uses a private application schema.
"""
import os
import json
from datetime import datetime, timezone
import sqlite3
from contextlib import contextmanager
from urllib.parse import urlparse, parse_qs
from uuid import uuid4


def database_target():
    if 'DEAL_FINDER_DATABASE_URL' in os.environ:
        target = os.environ['DEAL_FINDER_DATABASE_URL'].strip()
        if not target:
            raise ValueError('DEAL_FINDER_DATABASE_URL is empty')
        return target
    return os.getenv('DEAL_FINDER_DB', 'deal-finder.db')


def validate_postgres_url(target):
    parsed = urlparse(target)
    if parsed.scheme not in ('postgresql', 'postgres') or not parsed.hostname or not parsed.path.strip('/'):
        raise ValueError('PostgreSQL URL requires host and database name')
    query = parse_qs(parsed.query)
    mode = query.get('sslmode', ['require'])[0]
    if mode not in ('require', 'verify-ca', 'verify-full') and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Remote PostgreSQL requires TLS')
    if parsed.hostname.endswith('.pooler.supabase.com') and parsed.port == 6543:
        raise ValueError('Use Supabase session pooler on port 5432 or a direct connection')
    return mode


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor
    @property
    def rowcount(self):
        return self.cursor.rowcount
    def _row(self, row):
        if row is None:
            return None
        return tuple(x.astimezone(timezone.utc).isoformat() if isinstance(x, datetime) else x for x in row)
    def fetchone(self):
        return self._row(self.cursor.fetchone())
    def fetchall(self):
        return [self._row(x) for x in self.cursor.fetchall()]
    def __iter__(self):
        return (self._row(row) for row in self.cursor)


class Database:
    def __init__(self, target):
        self.dialect = 'postgres' if target.startswith(('postgresql://', 'postgres://')) else 'sqlite'
        self._transactions = []
        if self.dialect == 'sqlite':
            self.connection = sqlite3.connect(target)
            self.connection.execute('PRAGMA foreign_keys=ON')
        else:
            sslmode = validate_postgres_url(target)
            try:
                import psycopg
            except ImportError:
                raise RuntimeError('Install the postgres dependency: pip install -e ".[postgres]"') from None
            try:
                self.connection = psycopg.connect(target, autocommit=True, connect_timeout=10,
                    prepare_threshold=None, sslmode=sslmode, application_name='deal-finder')
                self.connection.execute('SET search_path TO deal_finder, pg_catalog')
            except psycopg.Error:
                raise RuntimeError('PostgreSQL connection failed; verify configured secrets and network access') from None

    def execute(self, sql, params=()):
        # Internal SQL uses ? placeholders; no arbitrary caller SQL reaches here.
        # Percent signs in bound JSON/URLs are never interpolated.
        if self.dialect == 'postgres':
            return Cursor(self.connection.execute(sql.replace('?', '%s'), params if params else None))
        return self.connection.execute(sql, params)

    def executescript(self, sql):
        if self.dialect == 'postgres':
            return self.connection.execute(sql, prepare=False)
        return self.connection.executescript(sql)

    def json_param(self, encoded):
        if self.dialect == 'postgres':
            from psycopg.types.json import Jsonb
            return Jsonb(json.loads(encoded))
        return encoded

    def json_decode(self, value):
        return value if self.dialect == 'postgres' else json.loads(value)

    def insert_id(self, sql, params):
        if self.dialect == 'postgres':
            return self.execute(sql + ' RETURNING id', params).fetchone()[0]
        return self.execute(sql, params).lastrowid

    def begin(self):
        self.execute('BEGIN' if self.dialect == 'postgres' else 'BEGIN IMMEDIATE')

    def batch_lock(self, batch_id):
        if self.dialect == 'postgres':
            self.execute('SELECT pg_advisory_xact_lock(hashtextextended(?, 0))', (batch_id,))

    def json_field(self, field, alias=''):
        allowed = {'make','model','generation','trim','fuel','transmission','province','seller_type','vehicle_id'}
        if field not in allowed or alias not in ('', 's'):
            raise ValueError('Unknown indexed JSON field')
        prefix = alias + '.' if alias else ''
        if self.dialect == 'postgres':
            return f"({prefix}payload ->> '{field}')"
        return f"json_extract({prefix}payload, '$.{field}')"

    @contextmanager
    def savepoint(self):
        name = 'record_' + uuid4().hex
        self.execute('SAVEPOINT ' + name)
        try:
            yield
        except Exception:
            self.execute('ROLLBACK TO SAVEPOINT ' + name)
            self.execute('RELEASE SAVEPOINT ' + name)
            raise
        else:
            self.execute('RELEASE SAVEPOINT ' + name)

    def __enter__(self):
        if self.dialect == 'postgres':
            context = self.connection.transaction()
            self._transactions.append(context)
            context.__enter__()
        else:
            self.connection.__enter__()
        return self

    def __exit__(self, *args):
        if self.dialect == 'postgres':
            return self._transactions.pop().__exit__(*args)
        return self.connection.__exit__(*args)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()

    def schema_ready(self):
        if self.dialect == 'sqlite':
            return True
        row = self.execute("SELECT to_regclass('deal_finder.jobs'), to_regclass('deal_finder.schema_migrations')").fetchone()
        return all(row)
