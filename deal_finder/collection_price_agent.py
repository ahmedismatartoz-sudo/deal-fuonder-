"""Cheap search-page price triage; never a purchase recommendation."""
import os
import time
from collections import OrderedDict
from datetime import datetime, timezone
from .price_memory import PriceMemory, amount_usable, market_restricted, normalized, same_variant
from .damage_screening import classify
from .margin_policy import minimum_net_margin_eur
from .agents.market_prices import assess

VERSION = 'collection-price-agent-v1'


def enabled():
    return os.getenv('DEAL_FINDER_AUTOSCOUT24_PRICE_SCREENING_ENABLED') == '1'


class CollectionPriceAgent:
    def __init__(self, db):
        self.memory = PriceMemory(db)
        self.cache = OrderedDict()

    def screen(self, target):
        result = dict(agent=VERSION, status='needs_market_evidence',
                      detail_fetch_recommended=False, buy_recommendation=False,
                      net_margin_eur=None, basis='published_asking_prices')
        if not amount_usable(target) or market_restricted(target):
            return dict(result, status='excluded', reason='unusable_total_price_or_market_restriction')
        if target.get('identity_dossier', {}).get('conflicts'):
            return dict(result, reason='identity_conflict')
        if classify(target)['category'] == 'severe':
            return dict(result, status='excluded', reason='severe_damage')
        if not all(target.get(k) for k in ('make', 'model', 'fuel', 'transmission')) or any(
                type(target.get(k)) is not int for k in ('year', 'mileage_km')):
            return dict(result, reason='missing_search_identity')
        key = (normalized(target['make']), normalized(target['model']))
        now = time.monotonic()
        cached = self.cache.get(key)
        if cached is None or now-cached[0] > 300:
            rows = list(self.memory.current(datetime.now(timezone.utc), make=key[0], model=key[1]))
            self.cache[key] = (now, rows)
            self.cache.move_to_end(key)
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
        rows = self.cache[key][1]
        cohort = [q for q in rows if amount_usable(q) and not market_restricted(q)
                  and classify(q)['category'] == 'clean'
                  and type(q.get('year')) is int and type(q.get('mileage_km')) is int
                  and q.get('seller_type') == target.get('seller_type')
                  and normalized(q.get('fuel')) == normalized(target['fuel'])
                  and normalized(q.get('transmission')) == normalized(target['transmission'])
                  and same_variant(target, q)
                  and (q['source'], q['source_id']) != (target.get('source'), target.get('source_id'))]
        peers = [q for q in cohort if abs(q['year']-target['year']) <= 2
                 and abs(q['mileage_km']-target['mileage_km']) <= 40000]
        # assess deduplicates reposts before counting or learning.
        context = assess(target, cohort, peers)
        if context['comparable_count'] < 3:
            return dict(result, reason='fewer_than_three_compatible_comparables', market_price_agent=context)
        gap = context['asking_low_eur']-target['price_eur']
        required = minimum_net_margin_eur(target['price_eur'])
        opportunity = gap >= required and gap*100 >= target['price_eur']*10
        return dict(result, status='apparent_opportunity' if opportunity else 'not_apparent_opportunity',
                    detail_fetch_recommended=opportunity, market_price_agent=context,
                    gross_headroom_before_all_costs_eur=gap,
                    minimum_required_net_margin_eur=required,
                    final_cost_and_damage_analysis_required=True)
