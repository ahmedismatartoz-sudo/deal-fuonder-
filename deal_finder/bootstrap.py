"""One initial page per worker iteration; persisted cursors survive restarts."""
import json
import os
import re
from .archive import Archive
from .autoscout24 import (VERSION, CollectionBlocked, CollectionBusy,
                         collect, validate_config)


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
    if status['mode'] != 'initial' or status['scope'] != expected:
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
