import os
import unittest
from unittest.mock import patch
import test_price_memory as fixtures
from test_market import complete
from test_core import NOW
from deal_finder.collection_price_agent import CollectionPriceAgent


class DiscoveryTests(unittest.TestCase):
    setUp=fixtures.PriceMemoryTests.setUp
    tearDown=fixtures.PriceMemoryTests.tearDown
    ingest=fixtures.PriceMemoryTests.ingest
    def records(self):
        common=dict(model='Panda',generation=None,trim=None,version_text=None,
                    fuel=None,transmission=None,condition='unknown')
        return [complete('cheap',price_eur=5000,**common)]+[
            complete('peer'+str(i),price_eur=8000+i*100,mileage_km=80000+i,**common) for i in range(3)]

    def test_unknown_condition_and_missing_variant_are_discovery_only(self):
        self.ingest(self.records())
        strict=self.memory.first_test('broad',NOW,profile='opportunities')
        broad=self.memory.first_test('broad',NOW,profile='discovery')
        self.assertEqual(strict['candidates'],[])
        self.assertEqual([p['source_id'] for p in broad['candidates']],['cheap'])
        lead=broad['candidates'][0]
        self.assertIn('condition_unknown',lead['uncertainty_flags'])
        self.assertFalse(lead['buy_recommendation'])
        self.assertIsNone(lead['net_margin_eur'])
        self.assertTrue(broad['final_conservative_filter_required'])

    def test_severe_damage_excluded_and_contradictions_flagged(self):
        records=self.records()
        records[0]['payload'].update(description='150 CV',power_hp=143)
        records.append(complete('severe',price_eur=1000,model='Panda',description='Motore rotto'))
        self.ingest(records)
        report=self.memory.first_test('conflicts',NOW,profile='discovery')
        self.assertEqual([p['source_id'] for p in report['candidates']],['cheap'])
        self.assertIn('identity_conflicts_to_review',report['candidates'][0]['uncertainty_flags'])
        self.assertEqual(report['exclusions']['known_severe_damage'],1)

    @patch.dict(os.environ,{'DEAL_FINDER_FIRST_TEST_PROFILE':'discovery'})
    def test_collector_uses_same_broad_price_signal(self):
        self.ingest(self.records())
        agent=CollectionPriceAgent(self.archive.db)
        target=next(p for p in self.memory.current(NOW) if p['source_id']=='cheap')
        with patch('deal_finder.collection_price_agent.datetime') as clock:
            clock.now.return_value=NOW
            result=agent.screen(target)
        self.assertEqual(result['status'],'apparent_opportunity')
        self.assertTrue(result['detail_fetch_recommended'])
        self.assertFalse(result['buy_recommendation'])

    def test_more_than_two_leads_per_family_are_retained(self):
        records=[complete('peer'+str(i),price_eur=10000+i*100,model='Panda',mileage_km=80000+i,
            fuel=None,transmission=None,condition='unknown') for i in range(12)]+[complete('cheap'+str(i),price_eur=4000+i*100,
            model='Panda',mileage_km=80000+i,condition='unknown') for i in range(8)]
        self.ingest(records)
        report=self.memory.first_test('many',NOW,profile='discovery',limit=100)
        self.assertGreater(len(report['candidates']),2)

    def test_missing_year_is_not_invented(self):
        records=self.records();records[0]['payload']['year']=None
        self.ingest(records)
        report=self.memory.first_test('missing',NOW,profile='discovery')
        self.assertNotIn('cheap',[p['source_id'] for p in report['candidates']])

    @patch.dict(os.environ,{'DEAL_FINDER_FIRST_TEST_PROFILE':'discovery'})
    def test_enrichment_triage_keeps_the_discovery_signal(self):
        from deal_finder.agents.market_triage import review
        self.ingest(self.records())
        target=next(p for p in self.memory.current(NOW) if p['source_id']=='cheap')
        result=review(target,self.archive.db,NOW)
        self.assertTrue(result['priority_enrichment'])
        self.assertFalse(result['exact_variant_verified'])

    def test_price_discount_tiers_include_exact_boundaries(self):
        from deal_finder.opportunity_discovery import signal
        for price,required in [(2000,2000),(5999,2000),(6000,3000),(9999,3000),
                               (10000,4000),(14999,4000),(15000,5000),(20000,5000)]:
            for gap,expected in [(required-1,False),(required,True)]:
                with self.subTest(price=price,gap=gap):
                    target=dict(source='export',source_id='target',make='Fiat',model='Panda',year=2018,
                        mileage_km=80000,price_eur=price,price_kind='total',observed_at=NOW.isoformat())
                    peers=[dict(target,source_id='peer'+str(i),mileage_km=80000+i,price_eur=price+gap) for i in range(2)]
                    result=signal(target,peers)
                    self.assertEqual(result['minimum_required_asking_discount_eur'],required)
                    self.assertEqual(result['apparent_opportunity'],expected)
                    self.assertIsNone(result['net_margin_eur'])

    def test_damaged_targets_never_compare_directly_with_healthy_or_unknown_cars(self):
        from deal_finder.opportunity_discovery import signal
        target=dict(source='export',source_id='target',make='Fiat',model='Panda',year=2018,
            mileage_km=80000,price_eur=2000,price_kind='total',condition='damaged',observed_at=NOW.isoformat())
        healthy=[dict(target,source_id='peer'+str(i),mileage_km=80000+i,price_eur=8000,condition='undamaged') for i in range(2)]
        self.assertIsNone(signal(target,healthy))
        self.assertIsNone(signal(target,[dict(p,condition='unknown') for p in healthy]))
        damaged=[dict(p,condition='damaged',price_eur=4500) for p in healthy]
        context=signal(target,damaged)
        self.assertTrue(context['apparent_opportunity'])
        self.assertEqual(context['damage_comparison_group'],'damaged_unspecified')
        self.assertFalse(context['damaged_and_healthy_compared_directly'])
