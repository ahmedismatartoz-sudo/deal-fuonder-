"""One initial page per worker iteration; persisted cursors survive restarts."""
import json
import os
import re
from .archive import Archive
from .autoscout24 import (VERSION, CollectionBlocked, CollectionBusy,
                         collect, validate_config, compatible_initial_scope)


def milano_config():
    """Private sellers first; then the full used-car market within 100 km."""
    makes = ('fiat', 'volkswagen', 'renault', 'peugeot', 'ford', 'opel',
             'toyota', 'audi', 'bmw', 'mercedes-benz', 'alfa-romeo', 'citroen',
             'dacia', 'hyundai', 'kia', 'nissan', 'seat', 'skoda', 'smart',
             'mini', 'suzuki', 'volvo', 'jeep', 'mazda', 'honda', 'mitsubishi')
    urls = []
    for private in (True, False):
        for year in range(2026, 1999, -1):
            for make in makes:
                url = (f'https://www.autoscout24.it/lst/{make}?zip=20121&zipr=100'
                       f'&fregfrom={year}&fregto={year}&pricefrom=1000&priceto=50000')
                urls.append(url + ('&custtype=P' if private else ''))
    return validate_config(dict(search_urls=urls, fetch_details=True,
                                delay_seconds=2, max_pages_per_search=200))


def bootstrap_config():
    value = os.getenv('DEAL_FINDER_AUTOSCOUT24_BOOTSTRAP')
    if not value:
        return None
    spec = json.loads(value)
    if not isinstance(spec, dict) or set(spec) not in ({'run_id', 'config'}, {'run_id', 'profile'}):
        raise ValueError('Bootstrap requires run_id and config or profile')
    if not isinstance(spec['run_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', spec['run_id']):
        raise ValueError('Invalid bootstrap run_id')
    if 'profile' in spec:
        if spec['profile'] != 'milano-100km':
            raise ValueError('Unknown bootstrap profile')
        return dict(run_id=spec['run_id'], config=milano_config())
    return dict(run_id=spec['run_id'], config=validate_config(spec['config']))


def bootstrap_status(archive, spec):
    try:
        status = archive.run_status('autoscout24', 'native-' + spec['run_id'])
    except KeyError:
        return dict(complete=False, pages=0, accepted=0, quarantined=0)
    expected = dict(country='IT', adapter=VERSION, configuration=spec['config'],
                    purpose='initial_base', snapshot_consistent=False)
    if not compatible_initial_scope(status, expected) and (status['mode'] != 'initial' or status['scope'] != expected):
        raise ValueError('Bootstrap configuration changed; use a new run_id')
    return status


class Bootstrap:
    def __init__(self, path, spec):
        self.path, self.spec = path, spec
        self.finished = False
        self.paused = False

    def step(self):
        if self.finished or self.paused:
            return None
        archive = Archive(self.path)
        try:
            try:
                result = collect(self.spec['config'], archive,
                                 run_id=self.spec['run_id'], mode='initial', max_pages=1)
            except CollectionBusy:
                return None  # No request to the source; the next iteration can retry.
            except CollectionBlocked as error:
                self.paused = True
                return dict(bootstrap='paused', run_id=self.spec['run_id'], reason=str(error),
                            checkpoint_retained=True, restart_required=True)
            progress = {k: result[k] for k in ('complete', 'pages', 'accepted', 'quarantined', 'next_cursor')}
            if result['complete']:
                from .market import Market
                from .queue import Queue
                market, queue = Market(self.path), Queue(self.path)
                try:
                    progress['market'] = market.scan(mode='initial', queue=queue)
                    progress['quality'] = archive.quality('autoscout24')
                finally:
                    market.close()
                    queue.close()
                self.finished = True
            return dict(bootstrap='complete' if self.finished else 'collecting',
                        run_id=self.spec['run_id'], **progress)
        finally:
            archive.close()


class OpportunityCollection:
    """Resume one incremental page at a time, including across UTC day changes."""
    def __init__(self, path, spec):
        self.path, self.spec = path, spec
        self.run_id = None
        self.last_step = None
        self.completed_day = None
        self.paused = False

    def step(self):
        import time
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat()
        if self.completed_day == today:
            return None
        from .autoscout24 import cycle_run_id
        from .collection_price_agent import VERSION as price_version
        if self.paused or (self.last_step is not None and time.monotonic()-self.last_step < 10):
            return None
        self.last_step = time.monotonic()
        archive = Archive(self.path)
        try:
            if not bootstrap_status(archive, self.spec)['complete']:
                return None
            if self.run_id is None:
                rows = archive.db.execute("SELECT run_id,scope,max(received_at) FROM collection_pages WHERE source=? AND mode=? GROUP BY run_id,scope ORDER BY max(received_at) DESC LIMIT 10", ('autoscout24','incremental')).fetchall()
                for run_id,scope,_ in rows:
                    scope=archive.db.json_decode(scope)
                    if (scope.get('configuration') == self.spec['config']
                            and scope.get('price_prescreen') == price_version
                            and not archive.run_status('autoscout24',run_id)['complete']):
                        self.run_id = run_id.removeprefix('native-')
                        break
                if self.run_id is None:
                    self.run_id = cycle_run_id(self.spec['config'],'incremental')
            try:
                result = collect(self.spec['config'],archive,run_id=self.run_id,mode='incremental',max_pages=1)
            except CollectionBusy:
                return None
            except CollectionBlocked as error:
                self.paused = True
                return dict(opportunity_collection='paused',reason=str(error),checkpoint_retained=True)
            progress = dict(opportunity_collection='complete' if result['complete'] else 'collecting',
                            run_id=self.run_id,**{k:result[k] for k in ('pages','accepted','quarantined','next_cursor')})
            if result['complete']:
                # Same completed cycle makes no requests until the next day.
                self.run_id = None
                self.completed_day = today
            return progress
        finally:
            archive.close()
