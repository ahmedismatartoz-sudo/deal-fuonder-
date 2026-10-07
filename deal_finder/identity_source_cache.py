"""Immutable compact source facts; never rescan original blobs during comparison."""
from datetime import timedelta
from .archive import canonical
from .vehicle_identity import compact_source_sql,source_fragment

VERSION='identity-source-fields-v1'


class IdentitySourceCache:
    def __init__(self,db):
        self.db=db
        if db.dialect=='sqlite':
            db.executescript('''CREATE TABLE IF NOT EXISTS identity_source_cache (
                version TEXT NOT NULL,source TEXT NOT NULL,source_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,seller_type TEXT,source_fields TEXT NOT NULL,
                PRIMARY KEY(version,source,source_id,observed_at));''')

    def missing_sql(self):
        return '''SELECT p.source,p.source_id,p.observed_at FROM price_observations p
            WHERE p.source='autoscout24' AND p.active=? AND p.observed_at BETWEEN ? AND ?
            AND NOT EXISTS (SELECT 1 FROM price_observations n WHERE n.source=p.source
                AND n.source_id=p.source_id AND n.observed_at>p.observed_at AND n.observed_at<=?)
            AND NOT EXISTS (SELECT 1 FROM identity_source_cache c WHERE c.version=?
                AND c.source=p.source AND c.source_id=p.source_id AND c.observed_at=p.observed_at)'''

    def args(self,as_of):
        return (True,(as_of-timedelta(days=30)).isoformat(),as_of.isoformat(),as_of.isoformat(),VERSION)

    def pending(self,as_of):
        return bool(self.db.execute(self.missing_sql()+' LIMIT 1',self.args(as_of)).fetchone())

    def sync(self,as_of,limit):
        keys=self.missing_sql()+' ORDER BY p.source,p.source_id,p.observed_at LIMIT ?'
        args=(*self.args(as_of),limit)
        if self.db.dialect=='postgres':
            sql='''WITH missing_keys AS MATERIALIZED ('''+keys+'''), originals AS MATERIALIZED (
                SELECT e.source,e.source_id,e.observed_at,e.payload#>>'{original,seller,type}' AS seller,
                    e.payload#>'{original,vehicle}' AS vehicle
                FROM missing_keys k JOIN listing_events e USING(source,source_id,observed_at)),
                inserted AS (INSERT INTO identity_source_cache(version,source,source_id,observed_at,seller_type,source_fields)
                SELECT ?,o.source,o.source_id,o.observed_at,o.seller,'''+compact_source_sql('postgres',vehicle_column='o.vehicle')+'''
                FROM originals o ON CONFLICT DO NOTHING RETURNING 1) SELECT count(*) FROM inserted'''
            return self.db.execute(sql,(*args,VERSION)).fetchone()[0]
        rows=self.db.execute('WITH missing_keys AS ('+keys+''') SELECT e.source,e.source_id,e.observed_at,e.payload
            FROM missing_keys k JOIN listing_events e USING(source,source_id,observed_at)''',args).fetchall()
        values=[]
        for source,identifier,observed,payload in rows:
            payload=self.db.json_decode(payload)
            seller=(payload.get('original') or {}).get('seller',{}).get('type')
            values.append((VERSION,source,identifier,observed,seller,canonical(source_fragment(payload))))
        with self.db:
            self.db.executemany('INSERT INTO identity_source_cache VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING',values)
        return len(values)
