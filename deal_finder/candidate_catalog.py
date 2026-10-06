"""Private provisional candidate cards for a future filtered interface."""
from datetime import datetime, timezone, timedelta
from .agents.handoff import filter_cards


def search(queue, *, offset=0, limit=100, as_of=None, **filters):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid candidate pagination')
    filter_cards([], **filters)  # Validate even when no cards exist.
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Candidate search requires timezone')
    seen, cards = set(), []
    exists = (queue.db.execute("SELECT to_regclass('deal_finder.listing_events')").fetchone()[0]
              if queue.db.dialect == 'postgres' else queue.db.execute(
                  "SELECT name FROM sqlite_master WHERE type='table' AND name='listing_events'").fetchone())
    if not exists:
        return dict(items=[], count=0, offset=offset, opportunities_verified=False, publication_enabled=False,
                    margin_basis='before_labor_and_other_costs')
    rows = queue.db.execute('SELECT as_of, inputs, outputs FROM agent_runs ORDER BY finished_at DESC, id DESC')
    for stamp, inputs, outputs in rows:
        inputs, output = queue.db.json_decode(inputs), queue.db.json_decode(outputs)
        raw = inputs.get('raw', {}).get('listing', {})
        if not isinstance(raw, dict) or not raw.get('source') or not raw.get('source_id'):
            continue
        key = raw['source'], raw['source_id']
        if key in seen:
            continue
        seen.add(key)
        if not timedelta(0) <= now-datetime.fromisoformat(stamp) <= timedelta(hours=24):
            continue
        card = output.get('candidate_card')
        if card is None or card.get('route') != 'verification':
            continue
        event = queue.db.execute('SELECT observed_at, active FROM listing_events WHERE source=? AND source_id=? ORDER BY observed_at DESC LIMIT 1', key).fetchone()
        if not event or not event[1] or event[0] != card.get('observed_at'):
            continue
        cards.append(dict(card, analysis_as_of=stamp, provisional=True, publishable=False))
    matches = filter_cards(cards, **filters)
    matches.sort(key=lambda c: (c.get('potential_gross_low_cents') is not None,
                               c.get('potential_gross_low_cents') or 0, c['observed_at']), reverse=True)
    return dict(items=matches[offset:offset+limit], count=len(matches), offset=offset,
                opportunities_verified=False, publication_enabled=False,
                margin_basis='before_labor_and_other_costs')
