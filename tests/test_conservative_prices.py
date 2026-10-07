import unittest
from deal_finder.conservative_prices import screen
from deal_finder.opportunity_discovery import signal, PeerIndex, build_report
from datetime import datetime, timezone


def car(source_id='target', price=4000, **changes):
    value = dict(source='export', source_id=source_id, make='Fiat', model='Panda',
                 year=2018, mileage_km=80000, price_eur=price, price_kind='total',
                 condition='undamaged', fuel='petrol', transmission='manual',
                 city='Milano', observed_at='2026-10-07T12:00:00+00:00')
    value.update(changes)
    return value


class ConservativePricesTests(unittest.TestCase):
    def peers(self, prices=(10000, 11000, 12000)):
        return [car(str(i), price, mileage_km=80001+i) for i, price in enumerate(prices)]

    def test_lowest_not_mean_less_stress_and_reserve(self):
        result = screen(car(), self.peers())
        self.assertTrue(result['price_priority_passed'])
        self.assertEqual(result['lowest_comparable_asking_eur'], 10000)
        self.assertEqual(result['conservative_exit_scenario_eur'], 8500)
        self.assertEqual(result['headroom_after_sale_stress_and_reserve_eur'], 3750)
        self.assertIsNone(result['net_margin_eur'])
        self.assertFalse(result['buy_recommendation'])
        self.assertIsNone(result['expected_days_to_sell'])

    def test_high_mean_cannot_disguise_low_market_floor(self):
        target = car(price=5000)
        result = signal(target, self.peers((6000, 12000, 14000)))
        self.assertTrue(result['apparent_opportunity'])
        self.assertFalse(result['conservative_price_screen']['price_priority_passed'])

    def test_two_broad_peers_retain_lead_without_strong_label(self):
        result = signal(car(), self.peers()[:2])
        self.assertTrue(result['apparent_opportunity'])
        self.assertFalse(result['conservative_price_screen']['price_priority_passed'])

    def test_distant_newer_peers_do_not_set_strong_reference(self):
        peers = [dict(p, year=2021, mileage_km=30000) for p in self.peers()]
        result = signal(car(), peers)
        self.assertTrue(result['apparent_opportunity'])
        self.assertIsNone(result['conservative_price_screen']['conservative_exit_scenario_eur'])

    def test_missing_fuel_or_conflict_does_not_qualify(self):
        for changes in ({'fuel': None}, {'identity_dossier': {'conflicts': ['engine']}}):
            with self.subTest(changes=changes):
                result = signal(car(**changes), self.peers())
                self.assertTrue(result['apparent_opportunity'])
                self.assertFalse(result['conservative_price_screen']['price_priority_passed'])

    def test_damaged_peer_prices_are_not_repaired_resale(self):
        target = car(condition='damaged')
        peers = [dict(p, condition='damaged') for p in self.peers()]
        result = signal(target, peers)['conservative_price_screen']
        self.assertFalse(result['price_priority_passed'])
        self.assertIsNone(result['conservative_exit_scenario_eur'])

    def test_exact_band_and_reserve_boundary(self):
        # New 5,000 boundary: 10,000 * .85 - 750 - 4,999 > 2,000,
        # but at 5,000 the requirement increases to 3,000.
        self.assertTrue(screen(car(price=4999), self.peers())['price_priority_passed'])
        self.assertFalse(screen(car(price=5000), self.peers())['price_priority_passed'])
        result = screen(car(price=6000), self.peers())
        self.assertEqual(result['minimum_required_net_margin_eur'], 3000)
        self.assertFalse(result['price_priority_passed'])

    def test_indexed_and_plain_remain_identical(self):
        peers = self.peers() + [car('far', 50000, year=2020)]
        target = car()
        plain = signal(target, peers)['conservative_price_screen']
        indexed = signal(target, PeerIndex(peers))['conservative_price_screen']
        self.assertEqual(plain, indexed)

    def test_report_retains_unqualified_leads_and_counts_priority_separately(self):
        rows = self.peers() + [car('strong'), car('mean-only', 6000)]
        result = build_report(rows,'test',datetime.now(timezone.utc),'test',{},20,True)
        ids = [p['source_id'] for p in result['candidates']]
        self.assertIn('strong', ids)
        self.assertIn('mean-only', ids)
        self.assertFalse(next(p for p in result['candidates'] if p['source_id']=='mean-only')['price_priority_passed'])
        self.assertEqual(result['approved_buys'], 0)
        self.assertEqual(result['total_price_priority_leads'],sum(p['price_priority_passed'] for p in result['candidates']))

    def test_reference_source_is_a_real_peer_not_target(self):
        result = screen(car(), self.peers())
        self.assertEqual(result['reference_source']['source_id'], '0')
        self.assertEqual(result['reference_source']['price_eur'], 10000)

