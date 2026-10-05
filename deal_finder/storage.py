import csv
import io
import json
import sqlite3
from .models import Listing


class Store:
    """Local development persistence. All imports validate before any write."""
    def __init__(self, path='deal-finder.db'):
        self.db = sqlite3.connect(path)
        self.db.execute('''CREATE TABLE IF NOT EXISTS snapshots (
            source TEXT NOT NULL, source_id TEXT NOT NULL, observed_at TEXT NOT NULL,
            payload TEXT NOT NULL, PRIMARY KEY(source, source_id, observed_at))''')

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
                payload = json.dumps(listing.to_dict(), sort_keys=True)
                previous = self.db.execute(
                    'SELECT payload FROM snapshots WHERE source=? AND source_id=? AND observed_at=?',
                    (*listing.identity, listing.observed_at)).fetchone()
                if previous:
                    if previous[0] != payload:
                        raise ValueError('Conflicting snapshot at the same observation time')
                    continue
                self.db.execute('INSERT INTO snapshots VALUES (?, ?, ?, ?)',
                                (*listing.identity, listing.observed_at, payload))
                inserted += 1
        return {'received': len(listings), 'inserted': inserted, 'duplicates': len(listings)-inserted}

    def listings(self):
        return [Listing.parse(json.loads(row[0])) for row in self.db.execute('SELECT payload FROM snapshots')]

    def close(self):
        self.db.close()
