import copy
import unittest
from test_core import row, NOW
from test_agents import envelope, pool, analyze
from deal_finder.models import Listing
from deal_finder.agents.market_experts import review
from deal_finder.agents.handoff import filter_cards
from deal_finder.agents import analyze as workflow


class ExpertTests(unittest.TestCase):
    def test_candidates_receive_tasks_and_filters(self):
        value = analyze(envelope(), pool(), NOW)
        self.assertEqual(value['handoff']['route'], 'verification')
        self.assertIn('research_parts_web', value['handoff']['tasks'])
        self.assertEqual(len(value['market_experts']['specialists']), 6)
        self.assertIsNone(value['candidate_card']['potential_gross_low_cents'])
        self.assertFalse(value['candidate_card']['publishable'])

    def test_sparse_and_unknown_enrich_instead_of_rejecting(self):
        for raw, candidates in ((envelope(), pool()[:2]),
                                (envelope(listing=row(99, condition='unknown')), pool())):
            value = analyze(raw, candidates, NOW)
            self.assertEqual(value['handoff']['route'], 'enrichment')
            self.assertEqual(value['parts_research']['status'], 'blocked')
        value = analyze(envelope(), pool()[:2], NOW)
        self.assertIsNotNone(value['market_experts']['specialists']['condition']['healthy_reference']['provisional_observed_envelope_eur'])

    def test_invalid_specs_never_search_other_models(self):
        raw = envelope(); del raw['listing']['generation']
        value = analyze(raw, pool(), NOW)
        self.assertIsNone(value['candidate_card'])
        self.assertEqual(value['handoff']['route'], 'enrichment')

    def test_damaged_and_healthy_separate(self):
        candidates = pool()+[Listing.parse(row(i+20, condition='damaged', price_eur=4000+i*10)) for i in range(8)]
        value = review(Listing.parse(row(99, condition='damaged')), candidates, NOW, dict(candidate=True))
        condition = value['specialists']['condition']
        self.assertLess(condition['as_is']['benchmark_eur'], condition['healthy_reference']['benchmark_eur'])
        self.assertIsNone(condition['repaired_resale_forecast_eur'])

    def test_mileage_bands_do_not_apply_depreciation(self):
        candidates = pool()+[Listing.parse(row(i+20, mileage_km=180000, price_eur=6000)) for i in range(8)]
        value = review(Listing.parse(row(99)), candidates, NOW, dict(candidate=True))
        mileage = value['specialists']['mileage']
        self.assertEqual(mileage['bands']['high']['benchmark']['benchmark_eur'], 6000)
        self.assertFalse(mileage['fixed_depreciation_applied'])

    def test_filters_unknown_margin_and_boundaries(self):
        cards = [dict(make='fiat', price_eur=1000, potential_gross_low_cents=None),
                 dict(make='fiat', price_eur=50000, potential_gross_low_cents=200000)]
        self.assertEqual(len(filter_cards(cards, min_price_eur=1000, max_price_eur=50000)), 2)
        self.assertEqual(len(filter_cards(cards, min_potential_gross_low_cents=0)), 1)
        with self.assertRaises(ValueError): filter_cards(cards, random_field=1)

    def test_parts_cannot_bypass_selection_or_identity(self):
        raw = envelope(parts_research=dict(vehicle=dict(vehicle_id='another'), parts=[]))
        self.assertEqual(analyze(raw, pool(), NOW)['parts_research']['status'], 'needs_review')
        raw['listing']['price_eur'] = 20000
        self.assertEqual(analyze(raw, pool(), NOW)['parts_research']['status'], 'blocked')

    def test_parts_ready_are_provisional_with_hours_separate(self):
        request = dict(vehicle=dict(vehicle_id='vehicle-99', make='fiat', model='panda',
                                    generation='319', year=2020, gearbox='manual'),
                       parts=[dict(id='lamp', name='left lamp', quantity=1, condition='new', tier='aftermarket',
                                   fallback=dict(low_cents=9000, high_cents=18000, basis='sourced analogy',
                                                 source_urls=['https://example.com/parts']),
                                   hours=dict(low_minutes=30, high_minutes=90, basis='synthetic assumption'))])
        value = analyze(envelope(parts_research=request), pool(), NOW)
        self.assertEqual(value['parts_research']['status'], 'provisional')
        card = value['candidate_card']
        self.assertEqual(card['parts_low_cents'], 9000)
        self.assertIsNone(card['labor_cost_cents'])
        self.assertEqual(card['hours'][0]['low_minutes'], 30)
        self.assertFalse(card['hours_additive'])
        self.assertEqual(card['potential_gross_low_cents'], (10100-8000)*100-18000)
        self.assertFalse(card['publishable'])

    def test_invalid_filter_validated_even_with_no_cards(self):
        for filters in (dict(min_price_eur=True),dict(min_price_eur=-1),
                        dict(min_mileage_km=100,max_mileage_km=50)):
            with self.assertRaises(ValueError): filter_cards([],**filters)
    def test_hours_cannot_override_repair_identifier(self):
        from deal_finder.agents.handoff import card
        target=Listing.parse(row(99))
        parts=dict(status='provisional',data=dict(parts_low_cents=1000,parts_high_cents=2000,
                   items=[dict(id='brakes',hours=dict(repair_id='forged',low_minutes=30,high_minutes=60,basis='assumption'))]))
        result=card(target,dict(route='verification'),{},parts)
        self.assertEqual(result['hours'][0]['repair_id'],'brakes')
