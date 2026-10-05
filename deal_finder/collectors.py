"""Resumable collection boundary. Live marketplace adapters are not configured."""
import hashlib
from dataclasses import dataclass
from typing import Protocol
from .archive import canonical


@dataclass(frozen=True)
class Page:
    records: list
    next_cursor: str | None
    complete: bool


class Connector(Protocol):
    source: str

    def fetch(self, *, cursor: str | None, mode: str, scope: dict) -> Page: ...


class ExportConnector:
    """An authorized export can contain incomplete original listings and removals."""
    def __init__(self, source, pages):
        if not isinstance(pages, list) or not pages or any(not isinstance(p, list) for p in pages):
            raise ValueError('Export requires a nonempty array of record pages')
        self.source, self.pages = source, pages

    def fetch(self, *, cursor, mode, scope):
        index = int(cursor) if cursor is not None else 0
        if not 0 <= index < len(self.pages):
            raise ValueError('Cursor outside export')
        complete = index == len(self.pages)-1
        return Page(self.pages[index], None if complete else str(index+1), complete)


class CollectionAgent:
    def execute(self, connector: Connector, archive, *, run_id, mode, scope, max_pages=100):
        if type(max_pages) is not int or not 1 <= max_pages <= 10000:
            raise ValueError('max_pages must be 1..10000')
        try:
            status = archive.run_status(connector.source, run_id)
        except KeyError:
            status = None
        if status:
            if status['mode'] != mode or status['scope'] != scope:
                raise ValueError('Cannot change a collection run configuration')
            if status['complete']:
                return status
        cursor = status['next_cursor'] if status else None
        for _ in range(max_pages):
            page = connector.fetch(cursor=cursor, mode=mode, scope=scope)
            page_id = hashlib.sha256(canonical(cursor).encode()).hexdigest()
            archive.ingest(dict(source=connector.source, run_id=run_id, page_id=page_id,
                                mode=mode, scope=scope, input_cursor=cursor, next_cursor=page.next_cursor,
                                complete=page.complete, records=page.records))
            status = archive.run_status(connector.source, run_id)
            if status['complete']:
                break
            cursor = status['next_cursor']
        return status


def sources():
    return [dict(source='export', status='available', modes=['initial', 'incremental'])] + [
        dict(source=s, status='adapter_not_configured', live_scraping=False)
        for s in ('facebook_marketplace', 'subito', 'autoscout24', 'automobile_it')]
