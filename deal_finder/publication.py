"""Build a reviewable feed from completed evaluations; never infer an approval."""
from datetime import datetime, timezone, timedelta
from .archive import instant


class PublicationAgent:
    def preview(self, queue, archive, batch_id, *, as_of=None):
        queue.batch(batch_id)
        now = as_of or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError('as_of requires timezone')
        # Keep the latest job per source identity within the requested edition.
        ids = [r[0] for r in queue.db.execute(
            'SELECT j.id FROM jobs j JOIN raw_records r ON r.id=j.raw_id WHERE r.batch_id=? ORDER BY j.id DESC',
            (batch_id,))]
        items, rejections, seen = [], [], set()
        for job_id in ids:
            result = queue.result(job_id)
            raw_row = queue.db.execute('SELECT r.payload FROM jobs j JOIN raw_records r ON r.id=j.raw_id WHERE j.id=?', (job_id,)).fetchone()
            raw = queue.db.json_decode(raw_row[0])
            listing = raw.get('listing', {}) if isinstance(raw, dict) else {}
            if not isinstance(listing, dict):
                listing = {}
            key = tuple(v if isinstance(v, str) else None for v in (listing.get('source'), listing.get('source_id')))
            if all(key):
                if key in seen:
                    continue
                seen.add(key)
            run = result['run']
            components = run['outputs'].get('components', {}) if run else {}
            supervisor = components.get('supervisor', {}).get('data', {})
            reasons = []
            if result['state'] != 'done' or not run:
                reasons.append('Analysis not completed')
            if supervisor.get('approved') is not True or not supervisor.get('checks') or not all(supervisor['checks'].values()):
                reasons.append('Supervisor approval or required checks missing')
            if components.get('publication', {}).get('data', {}).get('publishable') is not True:
                reasons.append('Publication gate closed')
            try:
                if run is None or not timedelta(0) <= now-instant(run['as_of']) <= timedelta(hours=24):
                    reasons.append('Analysis older than 24 hours or from the future')
                latest = archive.history(*key, limit=1)
                if not latest or not latest[0]['active'] or instant(latest[0]['observed_at']) != instant(listing['observed_at']):
                    reasons.append('Source snapshot unavailable, removed or superseded')
                elif not timedelta(0) <= now-instant(latest[0]['observed_at']) <= timedelta(hours=24):
                    reasons.append('Source observation older than 24 hours')
            except (ValueError, KeyError, TypeError):
                reasons.append('Source identity or timestamp invalid')
            # Future forecast adapters must supply concrete, positive net estimates.
            economics = components.get('opportunity', {}).get('data', {})
            profit = economics.get('forecast_profit_cents')
            if type(profit) is not int or profit <= 0:
                reasons.append('Positive documented forecast profit unavailable')
            if reasons:
                rejections.append(dict(job_id=job_id, source=key[0], source_id=key[1], reasons=reasons))
            else:
                items.append(dict(job_id=job_id, listing=latest[0], evaluation=components, run_id=run['id']))
        return dict(batch_id=batch_id, generated_at=now.astimezone(timezone.utc).isoformat(),
                    mode='preview', automatic_publication_enabled=False, items=items, rejected=rejections)
