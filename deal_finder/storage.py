import csv
import io
import json
from .models import Listing
from .database import Database, database_target


class Store:
    """Shared snapshot persistence; entire CSV/JSON imports remain atomic."""
    def __init__(self, path=None):
        self.db = Database(path if path is not None else database_target())
        if self.db.dialect == 'sqlite':
            self.db.execute('''CREATE TABLE IF NOT EXISTS snapshots (
                source TEXT NOT NULL, source_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(source, source_id, observed_at))''')
        elif not self.db.schema_ready():
            self.close()
            raise RuntimeError('PostgreSQL schema missing; run deal-finder migrate first')

    def snapshot(self, listing):
        payload = json.dumps(listing.to_dict(), sort_keys=True)
        inserted = self.db.execute('''INSERT INTO snapshots(source, source_id, observed_at, payload)
            VALUES (?, ?, ?, ?) ON CONFLICT(source, source_id, observed_at) DO NOTHING''',
            (*listing.identity, listing.observed_at, self.db.json_param(payload))).rowcount
        previous = self.db.execute('SELECT payload FROM snapshots WHERE source=? AND source_id=? AND observed_at=?',
                                   (*listing.identity, listing.observed_at)).fetchone()
        if self.db.json_decode(previous[0]) != listing.to_dict():
            raise ValueError('Conflicting immutable listing snapshot')
        return inserted

    def import_text(self, content, format='json'):
        if format == 'json':
            rows = json.loads(content)
            if not isinstance(rows, list):
                raise ValueError('JSON must be an array of listings')
        elif format == 'csv':
            rows = list(csv.DictReader(io.StringIO(content)))
        else:
            raise ValueError('format must be json or csv')
        listings = [Listing.parse(row) for row in rows]
        inserted = 0
        with self.db:
            for listing in listings:
                inserted += self.snapshot(listing)
        return {'received': len(listings), 'inserted': inserted, 'duplicates': len(listings)-inserted}

    def listings(self):
        return [Listing.parse(self.db.json_decode(row[0])) for row in self.db.execute('SELECT payload FROM snapshots')]

    def close(self):
        self.db.close()
