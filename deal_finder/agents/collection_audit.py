"""Bounded source-history audit; removal is not evidence of a completed sale."""


def execute(db, target, candidates, as_of):
    exists = (db.execute("SELECT to_regclass('deal_finder.listing_events')").fetchone()[0]
              if db.dialect == 'postgres' else db.execute(
                  "SELECT name FROM sqlite_master WHERE type='table' AND name='listing_events'").fetchone())
    table = 'listing_events' if exists else 'snapshots'
    total = db.execute('SELECT count(*) FROM '+table+' WHERE source=? AND source_id=? AND observed_at<=?',
                       (*target.identity, as_of.isoformat())).fetchone()[0]
    rows = db.execute('SELECT observed_at, payload'+(', active' if exists else '')+' FROM '+table+
                      ' WHERE source=? AND source_id=? AND observed_at<=? ORDER BY observed_at DESC LIMIT 100',
                      (*target.identity, as_of.isoformat())).fetchall()
    observations = []
    for record in reversed(rows):
        stamp,payload=record[:2]
        value = db.json_decode(payload)
        observations.append(dict(observed_at=stamp, price_eur=value.get('price_eur'),
                                 price_kind=value.get('price_kind', 'unknown'),
                                 active=bool(record[2]) if exists else value.get('active')))
    changes = []
    for old, new in zip(observations, observations[1:]):
        if (old['price_kind']==new['price_kind']=='total' and type(old['price_eur']) is int
                and type(new['price_eur']) is int and old['price_eur']!=new['price_eur']):
            changes.append(dict(observed_at=new['observed_at'], previous_price_eur=old['price_eur'],
                                price_eur=new['price_eur'], delta_eur=new['price_eur']-old['price_eur']))
    shared_photos, verified_duplicates = [], []
    photos = set(target.image_urls or [])
    for item in candidates:
        if item.identity == target.identity:
            continue
        if target.vehicle_id and item.vehicle_id == target.vehicle_id:
            verified_duplicates.append(item.url)
        elif photos and photos.intersection(item.image_urls or []):
            shared_photos.append(item.url)
    return dict(agent='collection_audit', version='collection-history-v1', observation_count=total,
                history=observations, history_window_limit=100, history_truncated=total>100,
                price_changes=changes, last_checked_at=target.observed_at,
                verified_identity_duplicates=sorted(set(verified_duplicates)),
                shared_photo_repost_signals=sorted(set(shared_photos)),
                shared_photos_prove_duplicate=False, removal_proves_sale=False,
                scope='same_source_history_and_current_comparable_pool',
                global_cross_source_duplicate_coverage_complete=False)
