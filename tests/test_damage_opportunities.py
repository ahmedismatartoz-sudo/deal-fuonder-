import unittest
import test_price_memory as base
from test_market import complete
from test_core import NOW
from deal_finder.damage_screening import classify,quotas,feasibility


class DamageScreeningTests(unittest.TestCase):
    def test_critical_faults_excluded_and_equipment_mentions_not_damage(self):
        for desc in ('Airbag esplosi','Motore da sostituire','Telaio piegato','Auto alluvionata','Problemi al cambio'):
            with self.subTest(desc=desc):self.assertEqual(classify(dict(condition='undamaged',description=desc))['category'],'severe')
        for desc in ('6 airbag, ABS, ESP','Mai incidentata, airbag non esplosi','Senza danni strutturali'):
            with self.subTest(desc=desc):self.assertEqual(classify(dict(condition='undamaged',description=desc))['category'],'clean')

    def test_minimal_moderate_unknown_and_negation(self):
        for description,expected in (('Lievi graffi','minimal'),('Piccole ammaccature','minimal'),
            ('Carrozzeria anteriore da riparare','non_severe'),('Paraurti da sostituire','non_severe'),
            ('Grandinata','non_severe'),('Non grandinata e senza graffi','clean')):
            self.assertEqual(classify(dict(condition='undamaged',description=description))['category'],expected)
        self.assertEqual(classify(dict(condition='damaged'))['category'],'unknown')
        self.assertEqual(classify(dict(condition='unknown'))['category'],'unknown')
        self.assertEqual(quotas(20),dict(clean=6,minimal=4,non_severe=10))

    def test_impossible_net_excluded_without_turning_unknown_costs_into_zero(self):
        result=feasibility(dict(price_eur=4800),dict(asking_low_eur=5500,confidence='high'))
        self.assertFalse(result['passes_necessary_budget']);self.assertIsNone(result['net_margin_eur'])
        good=feasibility(dict(price_eur=3500),dict(asking_low_eur=10000,confidence='low'))
        self.assertEqual(good['remaining_cost_budget_eur'],2250)
        self.assertFalse(good['costs_verified']);self.assertFalse(good['buy_recommendation'])


class OpportunitySelectionTests(unittest.TestCase):
    setUp=base.PriceMemoryTests.setUp
    tearDown=base.PriceMemoryTests.tearDown
    ingest=base.PriceMemoryTests.ingest
    def test_requested_mix_uses_clean_references_and_balances_price_bands(self):
        records=[]
        for n,(category,number,description,condition) in enumerate([
            ('clean',6,'Auto curata','undamaged'),('minimal',4,'Lievi graffi','undamaged'),
            ('non_severe',10,'Paraurti da riparare','damaged')]):
            for i in range(number):
                band=i%4;purchase=(2000,6000,11000,16000)[band];reference=(12000,18000,25000,35000)[band]
                common=dict(model=category+str(i),version_text='1.2 Easy',trim='Easy')
                records.append(complete(category+str(i),price_eur=purchase,description=description,condition=condition,**common))
                for j in range(4):records.append(complete(category+str(i)+'-peer'+str(j),price_eur=reference+j*50,mileage_km=50000+j,**common))
        records.append(complete('critical',price_eur=1000,description='Airbag esplosi'))
        self.ingest(records)
        report=self.memory.first_test('mix',NOW,profile='opportunities')
        self.assertEqual(len(report['candidates']),20)
        self.assertEqual({c['category']:c['selected'] for c in report['damage_mix']['categories']},quotas(20))
        self.assertEqual({p['price_band'] for p in report['candidates']},{0,1,2,3})
        self.assertNotIn('critical',[p['source_id'] for p in report['candidates']])
        for p in report['candidates']:
            self.assertTrue(p['economic_screen']['passes_necessary_budget'])
            self.assertFalse(p['buy_recommendation'])
            self.assertIsNone(p['net_margin_eur'])

    def test_shortage_is_explicit_and_not_filled_with_severe_or_unknown_damage(self):
        records=[complete('clean',price_eur=2000,version_text='1.2 Easy')]
        records += [complete('peer'+str(i),price_eur=12000+i*100,version_text='1.2 Easy',mileage_km=50000+i) for i in range(4)]
        self.ingest(records)
        report=self.memory.first_test('shortage',NOW,profile='opportunities')
        categories={c['category']:c for c in report['damage_mix']['categories']}
        self.assertEqual(categories['minimal']['selected'],0);self.assertEqual(categories['minimal']['shortage'],4)
        self.assertEqual(categories['non_severe']['shortage'],10)
        self.assertTrue(report['damage_mix']['shortages_are_not_filled_with_unverified_or_severe_vehicles'])

    def test_identity_conflict_is_excluded_from_training_and_targets(self):
        records=[complete('cheap',price_eur=2000,version_text='1.2 Easy',power_hp=69)]
        records += [complete('peer'+str(i),price_eur=12000+i*100,version_text='1.2 Easy',power_hp=69,
                   description='Motore 85 CV',mileage_km=50000+i) for i in range(4)]
        self.ingest(records)
        report=self.memory.first_test('conflicts',NOW,profile='opportunities')
        self.assertEqual(report['candidates'],[])
        self.assertEqual(report['identity_quality']['conflicted_observations'],4)
