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
            complete('peer'+str(i),price_eur=6500+i*100,mileage_km=80000+i,**common) for i in range(3)]

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
        records=[complete('peer'+str(i),price_eur=6500+i*100,model='Panda',mileage_km=80000+i,
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
