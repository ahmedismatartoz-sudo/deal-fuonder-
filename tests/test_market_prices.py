import unittest
from deal_finder.agents.market_prices import assess


class MarketPriceSkillsTests(unittest.TestCase):
    def dataset(self):
        return [dict(source='export',source_id=str(y)+'-'+str(k),year=y,mileage_km=k,
                     price_eur=10000+800*(y-2018)-(k-100000)//40,city='Milano',version_text='1.2 Easy')
                for y in range(2015,2022) for k in range(60000,150000,10000)]

    def test_learns_known_effects_and_validates_on_separate_vehicles(self):
        target=dict(source='export',source_id='cheap',year=2018,mileage_km=100000,price_eur=5000)
        data=self.dataset()
        nearby=[p for p in data if p['year']==2020 and p['mileage_km'] in (90000,100000,110000)]
        result=assess(target,data+[target],nearby)
        self.assertEqual(result['method'],'archive_trained_year_mileage')
        self.assertAlmostEqual(result['adjustments']['euro_per_newer_year'],800,places=1)
        self.assertAlmostEqual(result['adjustments']['euro_per_extra_10000_km'],-250,places=1)
        self.assertEqual(result['asking_low_eur'],10000)
        self.assertEqual(result['validation']['median_absolute_error_eur'],0)
        self.assertTrue(result['validation']['holdout_excluded_from_fit'])
        self.assertEqual(result['cohort_count'],len(data))
        self.assertFalse(result['buy_recommendation'])

    def test_sparse_sample_has_no_invented_depreciation(self):
        target=dict(source='export',source_id='cheap',year=2018,mileage_km=100000)
        data=self.dataset()[:3]
        result=assess(target,data,data)
        self.assertEqual(result['method'],'unadjusted_comparables')
        self.assertIsNone(result['adjustments'])
        self.assertIsNone(result['validation'])
        self.assertEqual(result['confidence'],'low')

    def test_valid_fit_without_independent_holdout_cannot_adjust_prices(self):
        target=dict(source='export',source_id='cheap',year=2018,mileage_km=100000)
        data=[p for p in self.dataset() if p['year'] in (2017,2018,2019) and p['mileage_km'] in (60000,80000,100000,120000,140000)]
        result=assess(target,data,data)
        self.assertEqual(len(data),15)
        self.assertEqual(result['method'],'unadjusted_comparables')
        self.assertIsNone(result['adjustments'])

    def test_does_not_extrapolate_outside_archive_coverage(self):
        target=dict(source='export',source_id='outside',year=2025,mileage_km=10000)
        data=self.dataset()
        result=assess(target,data,data[:5])
        self.assertEqual(result['method'],'unadjusted_comparables')
        self.assertIsNone(result['adjustments'])

    def test_reposts_do_not_inflate_learning_sample(self):
        target=dict(source='export',source_id='cheap',year=2018,mileage_km=100000)
        data=self.dataset()[:3]
        duplicate=[dict(data[0],source_id='copy'+str(i)) for i in range(30)]
        result=assess(target,data+duplicate,data+duplicate)
        self.assertEqual(result['cohort_count'],3)
        self.assertEqual(result['comparable_count'],3)
        self.assertEqual(result['method'],'unadjusted_comparables')
