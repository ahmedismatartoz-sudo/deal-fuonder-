"""Private provisional candidate cards for a future filtered interface."""
from datetime import datetime, timezone, timedelta
from .agents.handoff import filter_cards, FILTER_FIELDS


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
    if queue.db.dialect == 'postgres':
        raw_field, card_field = "inputs #> '{raw,listing}'", "outputs -> 'candidate_card'"
    else:
        raw_field, card_field = "json_extract(inputs, '$.raw.listing')", "json_extract(outputs, '$.candidate_card')"
    cursor = now.isoformat(), 2**63-1
    while True:
        rows = queue.db.execute('SELECT id, finished_at, as_of, '+raw_field+', '+card_field+''' FROM agent_runs
            WHERE finished_at<? OR (finished_at=? AND id<?) ORDER BY finished_at DESC, id DESC LIMIT 50''',
            (cursor[0], cursor[0], cursor[1])).fetchall()
        for run_id, finished, stamp, raw, card in rows:
            cursor = finished, run_id
            raw = queue.db.json_decode(raw) if raw is not None else None
            card = queue.db.json_decode(card) if card is not None else None
            if not isinstance(raw, dict) or not raw.get('source') or not raw.get('source_id'):
                continue
            key = raw['source'], raw['source_id']
            if key in seen:
                continue
            seen.add(key)
            if not timedelta(0) <= now-datetime.fromisoformat(stamp) <= timedelta(hours=24):
                continue
            if card is None or card.get('route') != 'verification':
                continue
            event = queue.db.execute('SELECT observed_at, active FROM listing_events WHERE source=? AND source_id=? ORDER BY observed_at DESC LIMIT 1', key).fetchone()
            if not event or not event[1] or event[0] != card.get('observed_at'):
                continue
            # Retain only filter/rank metadata across the whole result set.
            # Descriptions and photos are loaded for the requested page below.
            cards.append(dict({k:card.get(k) for k in FILTER_FIELDS}, _run_id=run_id, _analysis_as_of=stamp))
        if len(rows) < 50:
            break
    matches = filter_cards(cards, **filters)
    matches.sort(key=lambda c: (c.get('professional_policy_ready') is True,
                               c.get('conservative_margin_low_cents') is not None,
                               c.get('conservative_margin_low_cents') if c.get('conservative_margin_low_cents') is not None
                               else c.get('potential_gross_low_cents') or 0, c['observed_at']), reverse=True)
    page = []
    for metadata in matches[offset:offset+limit]:
        row = queue.db.execute('SELECT '+card_field+' FROM agent_runs WHERE id=?', (metadata['_run_id'],)).fetchone()
        card = queue.db.json_decode(row[0])
        page.append(dict(card, analysis_as_of=metadata['_analysis_as_of'], provisional=True, publishable=False))
    return dict(items=page, count=len(matches), offset=offset,
                opportunities_verified=False, publication_enabled=False,
                conservative_margin_basis='all_reviewed_costs_holding_and_combined_adverse_stress',
                margin_basis='before_labor_and_other_costs')
