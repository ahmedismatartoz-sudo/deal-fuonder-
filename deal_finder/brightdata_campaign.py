"""One durable, bounded Facebook market-sample campaign on the existing worker."""
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from .archive import Archive, canonical
from .brightdata import (CONTROL, SOURCE, DETAIL_DATASET, ProviderError, cycle,
                         paused_diagnostic)
from .vehicle_searches import FAMILIES

CAMPAIGN_SOURCE = 'brightdata_campaign'
PLAN_VERSION = 'milano-diverse-v1'
BANDS = ((500, 4999), (5000, 9999), (10000, 14999), (15000, 19999))


def search_plan():
    # Rotate brands before taking the next model, then rotate price bands.
    models = [(make, items[i]) for i in range(max(map(len, FAMILIES.values())))
              for make, items in FAMILIES.items() if i < len(items)]
    plan = []
    for i, (make, model) in enumerate(models):
        query = make + ' ' + model
        for offset in range(4):
            low, high = BANDS[(i + offset) % 4]
            plan.append(dict(query=query, low=low, high=high, purpose='market_comparables'))
            if len(plan) % 5 == 4:
                # Comparables stay the majority of the sample. These are search
                # signals, not proof of seller type, damage or profitability.
                signal = ('urgente', 'da sistemare', 'privato', 'incidentata')[(i + offset) % 4]
                plan.append(dict(query=query + ' ' + signal, low=500, high=19999,
                                 purpose='opportunity_discovery'))
    return plan


def config_for(spec, index, limit):
    search = search_plan()[index]
    url = 'https://www.facebook.com/marketplace/milan/search/?' + urlencode({
        'query': search['query'], 'minPrice': search['low'], 'maxPrice': search['high'],
        'radius': 5, 'exact': 'true'})
    return dict(cycle_id=spec['campaign_id'] + '-b' + str(index).zfill(4),
                dataset_id=DETAIL_DATASET, discover_by='url', input=[dict(url=url, country='IT')],
                limit=limit, schema_verified=True)


def validate_spec(spec):
    if (not isinstance(spec, dict) or set(spec) != {'campaign_id', 'credit_ceiling', 'batch_limit', 'plan_version'}
            or not isinstance(spec['campaign_id'], str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,70}', spec['campaign_id'])
            or type(spec['credit_ceiling']) is not int or not 1 <= spec['credit_ceiling'] <= 5000
            or type(spec['batch_limit']) is not int or not 1 <= spec['batch_limit'] <= 100
            or spec['plan_version'] != PLAN_VERSION):
        raise ValueError('Invalid bounded Facebook campaign configuration')
    return spec


def credit_ledger(archive, as_of=None):
    """Count all monthly connector records; hold caps for unresolved triggers.

    Provider credits shared with other products/clients remain unknowable here.
    The independently required unfunded-account gate is the external hard stop.
    """
    now = as_of or datetime.now(timezone.utc)
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    rows = archive.db.execute('SELECT source, run_id, page_id, scope, complete, accepted, rejected '
                              'FROM collection_pages WHERE source IN (?, ?) AND received_at>=?',
                              (CONTROL, SOURCE, month)).fetchall()
    imports, snapshots = {}, {}
    reservations = []
    for source, run_id, page_id, scope, complete, accepted, rejected in rows:
        if source == SOURCE and complete and run_id.startswith('brightdata-s'):
            imports[run_id] = accepted + rejected
        elif source == CONTROL:
            if page_id == 'reservation':
                config = archive.db.json_decode(scope)['configuration']
                reservations.append((run_id, config['limit']))
            elif complete:
                snapshots[run_id] = page_id
    used = 0
    for run_id, cap in reservations:
        if type(cap) is not int or not 1 <= cap <= 100:
            raise ValueError('Unsupported provider credit reservation')
        snapshot = snapshots.get(run_id)
        used += imports.get('brightdata-' + snapshot, cap) if snapshot else cap
    return used


def unique_count(archive):
    return archive.db.execute('SELECT COUNT(DISTINCT source_id) FROM listing_events WHERE source=?',
                              (SOURCE,)).fetchone()[0]


class BackgroundCampaign:
    def __init__(self, path, spec, *, client=None):
        self.path, self.spec = path, validate_spec(spec)
        self.client = client
        self.last_poll = None
        self.finished = False

    def checkpoint(self, archive, state, page_id, old_cursor, *, complete=False):
        cursor = None if complete else canonical(state)
        result = archive.ingest(dict(source=CAMPAIGN_SOURCE, run_id=self.spec['campaign_id'],
            page_id=page_id, mode='initial', scope=dict(country='IT', city='Milano', configuration=self.spec),
            input_cursor=old_cursor, next_cursor=cursor, complete=complete, records=[]))
        return result['next_cursor']

    def step(self):
        if self.finished or (self.last_poll is not None and time.monotonic() - self.last_poll < 60):
            return None
        self.last_poll = time.monotonic()
        archive = Archive(self.path)
        try:
            try:
                return self.advance(archive)
            except (ValueError, ProviderError) as error:
                self.finished = True
                return dict(paused_diagnostic(error), campaign_id=self.spec['campaign_id'])
        finally:
            archive.close()

    def advance(self, archive):
        try:
            saved = archive.run_status(CAMPAIGN_SOURCE, self.spec['campaign_id'])
        except KeyError:
            saved = None
        if saved and saved['scope']['configuration'] != self.spec:
            raise ValueError('Campaign configuration changed')
        if saved and saved['complete']:
            self.finished = True
            return dict(brightdata='campaign_complete', campaign_id=self.spec['campaign_id'])
        cursor = saved['next_cursor'] if saved else None
        state = json.loads(cursor) if cursor else dict(index=0, stage='next', rows=0, batches=0,
            unique_start=unique_count(archive), zero_yield_batches=0)
        if state['stage'] == 'paused':
            self.finished = True
            return dict(brightdata='campaign_paused', reason=state['reason'], campaign_id=self.spec['campaign_id'])
        if state['stage'] == 'next':
            remaining = self.spec['credit_ceiling'] - credit_ledger(archive)
            if remaining <= 0 or state['index'] >= len(search_plan()):
                reason = 'credit_ceiling_reached' if remaining <= 0 else 'search_plan_exhausted'
                self.checkpoint(archive, state, 'finished', cursor, complete=True)
                self.finished = True
                return dict(brightdata='campaign_complete', reason=reason, rows=state['rows'],
                            new_unique=unique_count(archive) - state['unique_start'])
            state.update(stage='inflight', limit=min(self.spec['batch_limit'], remaining),
                         unique_before=unique_count(archive))
            cursor = self.checkpoint(archive, state, 'allocated-' + str(state['index']), cursor)
        config = config_for(self.spec, state['index'], state['limit'])
        result = cycle(config, archive, client=self.client)
        status = result['status']
        if status in ('failed', 'recovery_required'):
            state.update(stage='paused', reason=status)
            self.checkpoint(archive, state, 'paused-' + str(state['index']), cursor)
            self.finished = True
        elif status == 'complete':
            counts = result['collection']
            raw_count = counts['accepted'] + counts['quarantined']
            new_count = max(0, unique_count(archive) - state['unique_before'])
            state['last_batch'] = dict(search_plan()[state['index']], returned=raw_count,
                accepted=counts['accepted'], excluded=counts['quarantined'], new_unique=new_count)
            state['rows'] += raw_count
            state['batches'] += 1
            state['zero_yield_batches'] = state['zero_yield_batches'] + 1 if raw_count and not new_count else 0
            state['index'] += 1
            state['stage'] = 'next'
            if state['zero_yield_batches'] >= 5:
                state.update(stage='paused', reason='five_nonempty_batches_without_new_valid_cars')
                self.finished = True
            self.checkpoint(archive, state, 'completed-' + str(state['index'] - 1), cursor)
        label = 'campaign_batch_complete' if status == 'complete' else 'campaign_' + status
        if state['stage'] == 'paused':
            label = 'campaign_paused'
        return dict(brightdata=label, campaign_id=self.spec['campaign_id'], reason=state.get('reason'),
                    batch=state['index'], snapshot_id=result.get('snapshot_id'), rows=state['rows'],
                    new_unique=max(0, unique_count(archive) - state['unique_start']),
                    credit_units_reserved_or_returned=credit_ledger(archive),
                    credit_ceiling=self.spec['credit_ceiling'])
