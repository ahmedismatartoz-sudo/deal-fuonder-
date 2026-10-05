"""Read completed, authorized Apify tasks; scraping schedules live in Apify.

GET only: this adapter does not launch paid runs. Token is sent in a header,
never a URL. Source-specific fields must be mapped explicitly, without inventing
vehicle specifications, price types, inspection results, or identity proofs.
"""
import os
import re
import json
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from .collectors import CollectionAgent, Page
from .models import Listing, normalize
from .archive import instant
from dataclasses import fields
from decimal import Decimal, InvalidOperation


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('Apify requires a resource ID, not a URL')
    return value


class ApifyClient:
    def __init__(self, token=None):
        self.token = token or os.getenv('APIFY_API_TOKEN')
        if not self.token:
            raise ValueError('Configure APIFY_API_TOKEN in the service environment')
        if not isinstance(self.token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,1024}', self.token):
            raise ValueError('APIFY_API_TOKEN has an invalid format')
        self.opener = build_opener(NoRedirect())

    def get(self, path, **query):
        url = 'https://api.apify.com/v2/' + path
        if query:
            url += '?' + urlencode(query)
        request = Request(url, headers={'Authorization': 'Bearer ' + self.token,
                                       'Accept': 'application/json'})
        try:
            with self.opener.open(request, timeout=30) as response:
                body = response.read(20_000_001)
                if len(body) > 20_000_000:
                    raise ValueError('Apify page exceeds 20 MB; reduce page_size')
                return json.loads(body)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            # Provider errors may echo request headers, so never log their body.
            raise RuntimeError('Apify request failed; check task access and retry the same cycle') from None

    def runs(self, task_id):
        offset = 0
        while True:
            data = self.get('actor-tasks/' + identifier(task_id) + '/runs',
                            offset=offset, limit=100, desc='true', status='SUCCEEDED')['data']
            items = data['items']
            for run in items:
                yield run
            offset += len(items)
            if offset >= data['total']:
                return
            if not items:
                raise RuntimeError('Apify run pagination did not advance')


def path_value(item, path):
    if not isinstance(path, str) or not path:
        raise ValueError('Mapping paths must be nonempty strings')
    value = item
    for component in path.split('.'):
        if isinstance(value, list) and component.isdigit():
            index = int(component)
            value = value[index] if index < len(value) else None
        elif isinstance(value, dict):
            value = value.get(component)
        else:
            return None
    return value


def mapped_event(item, *, source, observed_at, mapping):
    if not isinstance(item, dict):
        return {'payload': {'original': item}, 'adapter_error': 'Record is not an object'}
    allowed = {f.name for f in fields(Listing)} - {'source', 'observed_at', 'vehicle_id'}
    if set(mapping) - allowed:
        raise ValueError('Mapping contains unsupported or unverified fields')
    payload = {'original': item, 'adapter': 'apify-field-map-v1'}
    for field, spec in mapping.items():
        if isinstance(spec, str):
            spec = {'path': spec}
        if not isinstance(spec, dict) or set(spec) - {'path', 'values', 'require'}:
            raise ValueError('Mapping supports path, explicit value translations and unit guards')
        if 'require' in spec:
            guard = spec['require']
            if not isinstance(guard, dict) or set(guard) != {'path', 'value'}:
                raise ValueError('require needs a path and value')
            if path_value(item, guard['path']) != guard['value']:
                continue
        value = path_value(item, spec['path'])
        if value is None:
            continue
        if 'values' in spec:
            if not isinstance(spec['values'], dict):
                raise ValueError('values must be an object')
            value = spec['values'].get(str(value))
            if value is None:
                continue
        payload[field] = value
        if field in ('price_eur', 'year', 'mileage_km') and isinstance(value, str) and value.isdigit():
            payload[field] = int(value)
        elif field == 'price_eur' and type(value) in (str, float):
            try:
                amount = Decimal(str(value))
                if amount.is_finite() and 0 < amount <= 10_000_000 and amount == amount.to_integral_value():
                    payload[field] = int(amount)
            except InvalidOperation:
                pass
    source_id, url = payload.pop('source_id', None), payload.pop('url', None)
    if type(source_id) is int:
        source_id = str(source_id)
    active = payload.pop('active', True)
    # Missing price_kind remains unknown, missing condition/specifications remain
    # unusable. Description text alone cannot establish them with precision.
    return dict(source_id=source_id, url=url, active=active,
                observed_at=observed_at, payload=payload)


class ApifyDatasetConnector:
    def __init__(self, client, *, source, run_id, mapping, page_size=500):
        if type(page_size) is not int or not 1 <= page_size <= 5000:
            raise ValueError('page_size must be 1..5000')
        self.client, self.source, self.mapping = client, normalize(source), mapping
        run = client.get('actor-runs/' + identifier(run_id))['data']
        if run['id'] != run_id or run['status'] != 'SUCCEEDED' or not run.get('finishedAt'):
            raise ValueError('Only successfully completed Apify runs may be imported')
        # Dataset rows may have been collected hours before a large run finished.
        # The run start is a conservative freshness bound, not a creation date.
        self.observed_at = instant(run['startedAt']).isoformat()
        if instant(run['startedAt']) > instant(run['finishedAt']):
            raise ValueError('Invalid Apify run timestamps')
        self.dataset_id = identifier(run['defaultDatasetId'])
        self.total = client.get('datasets/' + self.dataset_id)['data']['itemCount']
        if type(self.total) is not int or self.total < 0:
            raise ValueError('Invalid Apify dataset size')
        self.page_size = page_size

    def fetch(self, *, cursor, mode, scope):
        offset = int(cursor) if cursor is not None else 0
        if not 0 <= offset <= self.total:
            raise ValueError('Cursor outside dataset')
        items = self.client.get('datasets/' + self.dataset_id + '/items',
                                offset=offset, limit=min(self.page_size, max(1, self.total-offset)),
                                format='json', clean='false', desc='false')
        if not isinstance(items, list) or len(items) != min(self.page_size, self.total-offset):
            raise RuntimeError('Dataset changed or was truncated; collection remains incomplete')
        records = []
        for item in items:
            try:
                records.append(mapped_event(item, source=self.source,
                                            observed_at=self.observed_at, mapping=self.mapping))
            except (ValueError, KeyError, TypeError) as error:
                records.append({'payload': {'original': item}, 'adapter_error': str(error)})
        end = offset + len(items)
        return Page(records, str(end) if end < self.total else None, end == self.total)


def validate_config(config):
    if not isinstance(config, dict) or set(config) != {'tasks'} or not isinstance(config['tasks'], list) or not config['tasks']:
        raise ValueError('Cycle config requires a nonempty tasks list')
    seen = set()
    allowed_fields = {f.name for f in fields(Listing)} - {'source', 'observed_at', 'vehicle_id'}
    for task in config['tasks']:
        if not isinstance(task, dict) or set(task) - {'task_id', 'source', 'mode', 'scope', 'mapping', 'page_size'}:
            raise ValueError('Invalid task configuration')
        task_id = identifier(task['task_id'])
        if task_id in seen:
            raise ValueError('A task may only be configured once')
        seen.add(task_id)
        normalize(task['source'])
        if task['mode'] not in ('initial', 'incremental', 'refresh') or task['scope'].get('country') != 'IT':
            raise ValueError('Task requires a mode and Italy scope')
        mapping = task['mapping']
        if not isinstance(mapping, dict) or not {'source_id', 'url'} <= set(mapping) or set(mapping) - allowed_fields:
            raise ValueError('Task requires an explicit source field map including ID and URL')
        for spec in mapping.values():
            if isinstance(spec, str):
                path_value({}, spec)
            elif isinstance(spec, dict) and set(spec) <= {'path', 'values', 'require'}:
                path_value({}, spec['path'])
                if 'values' in spec and not isinstance(spec['values'], dict):
                    raise ValueError('values must be an object')
                if 'require' in spec:
                    guard = spec['require']
                    if not isinstance(guard, dict) or set(guard) != {'path', 'value'}:
                        raise ValueError('require needs a path and value')
                    path_value({}, guard['path'])
            else:
                raise ValueError('Unsupported field mapping')
        size = task.get('page_size', 500)
        if type(size) is not int or not 1 <= size <= 5000:
            raise ValueError('Invalid page size')
    return config


def collect_tasks(config, archive, *, mode='incremental', client=None):
    """Import every unseen successful run, including gaps after service downtime.

    Initial tasks are excluded from the daily cycle. Refresh tasks collect only
    explicitly configured existing ads; absence from a new-ads run never removes
    anything. A task's remote schedule/search filters are configured in Apify.
    """
    validate_config(config)
    if mode not in ('initial', 'incremental'):
        raise ValueError('Invalid collection mode')
    selected = [task for task in config['tasks'] if
                (task['mode'] == 'initial' if mode == 'initial' else task['mode'] in ('incremental', 'refresh'))]
    if not selected:
        raise ValueError('No tasks configured for this mode')
    client = client or ApifyClient()
    statuses = []
    for task in selected:
        # Runs are captured before ingestion and processed oldest first. No
        # watermark hides unfinished older runs or delays/backdated creations.
        runs = [run for run in client.runs(task['task_id']) if run['status'] == 'SUCCEEDED']
        for run in sorted(runs, key=lambda run: (run['finishedAt'], run['id'])):
            run_id = 'apify-' + identifier(run['id'])
            try:
                saved = archive.run_status(task['source'], run_id)
            except KeyError:
                saved = None
            scope = dict(task['scope'], apify_task_id=task['task_id'],
                         adapter_mapping=task['mapping'], purpose=task['mode'])
            if saved and (saved['mode'] != mode or saved['scope'] != scope):
                raise ValueError('Cannot change the mapping or scope of an imported run')
            if saved and saved['complete']:
                continue
            connector = ApifyDatasetConnector(client, source=task['source'], run_id=run['id'],
                                              mapping=task['mapping'], page_size=task.get('page_size', 500))
            status = CollectionAgent().execute(connector, archive, run_id=run_id,
                mode=mode, scope=scope, max_pages=10000)
            statuses.append(status)
            if not status['complete']:
                raise RuntimeError('Collection incomplete; retry the cycle before screening')
    return statuses
