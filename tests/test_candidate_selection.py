import unittest
from datetime import datetime, timezone
from deal_finder.candidate_selection import select, judge, field_profile
from deal_finder.opportunity_discovery import peers_for, PeerIndex

NOW = datetime(2026,10,7,12,tzinfo=timezone.utc)


def lead(i, band=0, **changes):
    value = dict(source='autoscout24', source_id=str(i), make='Fiat', model='model'+str(i),
                 price_eur=[3000,8000,12000,17000][band], price_band=band,
                 year=2018, mileage_km=80000, fuel='petrol', transmission='manual',
                 version_text='1.2', city='Milano', seller_type='private',
                 description='Source description', detail_fetched=True, price_kind='total',
                 observed_at=NOW.isoformat(), damage_category='clean', price_priority_passed=False,
                 comparable_count=12, minimum_required_net_margin_eur=[2000,3000,4000,5000][band],
                 conservative_price_screen=dict(lowest_comparable_asking_eur=20000,
                    comparable_count=6,headroom_after_sale_stress_and_reserve_eur=10000))
    value.update(changes)
    return value


class SelectionTests(unittest.TestCase):
    def test_300_are_balanced_75_per_band_when_available(self):
        rows=[lead(b*100+i,b) for b in range(4) for i in range(100)]
        selected,meta=select(rows,300,NOW)
        self.assertEqual(len(selected),300)
        self.assertEqual(meta['selected_per_price_band'],[75]*4)
        self.assertEqual(meta['redistributed_slots'],0)
        self.assertTrue(all(p['candidate_judgment']['buy_recommendation'] is False for p in selected))

    def test_band_shortages_are_redistributed_without_fabricating(self):
        rows=[lead(i) for i in range(350)]+[lead(1000+i,3) for i in range(5)]
        selected,meta=select(rows,300,NOW)
        self.assertEqual(meta['selected_per_price_band'],[295,0,0,5])
        self.assertEqual(len(selected),300)
        self.assertEqual(meta['unfilled_band_targets_before_redistribution'],[0,75,75,70])
        short,_=select(rows[:15],300,NOW)
        self.assertEqual(len(short),15)

    def test_strong_price_candidates_remain_first_and_are_not_lost_to_mix(self):
        rows=[lead(i,0) for i in range(100)]+[lead(100+i,3,price_priority_passed=True) for i in range(80)]
        selected,_=select(rows,100,NOW)
        self.assertEqual(sum(p['price_priority_passed'] for p in selected),80)
        self.assertTrue(selected[0]['price_priority_passed'])

    def test_quality_and_conflicts_change_order_without_perfect_trim(self):
        good=lead(1,trim=None,generation=None)
        conflict=lead(2,identity_dossier={'conflicts':['engine']})
        unknown=lead(3,description=None,transmission='unknown',damage_category='unknown')
        self.assertGreater(judge(good,NOW)['score'],judge(conflict,NOW)['score'])
        self.assertGreater(judge(good,NOW)['score'],judge(unknown,NOW)['score'])
        selected,_=select([unknown,conflict,good],3,NOW)
        self.assertEqual(selected[0]['source_id'],'1')

    def test_high_mean_alone_never_improves_price_score(self):
        a=lead(1,conservative_price_screen={})
        b=dict(a,gross_headroom_before_all_costs_eur=100000,asking_typical_eur=200000)
        self.assertEqual(judge(a,NOW)['score'],judge(b,NOW)['score'])

    def test_profile_distinguishes_unknown_and_container_from_verification(self):
        rows=[lead(1,condition='unknown'),lead(2,condition='undamaged')]
        value=field_profile(rows)
        self.assertEqual(value['fields']['condition']['usable'],1)
        self.assertEqual(value['sample_count'],2)
        self.assertTrue(value['images_not_measured_in_compact_price_projection'])

    def test_order_is_deterministic_and_source_ids_are_unique(self):
        rows=[lead(i,i%4) for i in range(80)]
        a,_=select(rows,40,NOW);b,_=select(list(reversed(rows)),40,NOW)
        self.assertEqual([p['source_id'] for p in a],[p['source_id'] for p in b])
        self.assertEqual(len({p['source_id'] for p in a}),len(a))

    def test_family_preference_relaxes_only_as_needed(self):
        rows=[lead(i,model='Panda') for i in range(50)]
        selected,meta=select(rows,30,NOW)
        self.assertEqual(len(selected),30)
        self.assertTrue(meta['family_cap_relaxed'])

    def test_future_or_malformed_observation_gets_no_freshness_bonus(self):
        for observed in ('invalid','2027-10-07T12:00:00+00:00'):
            value=judge(lead(1,observed_at=observed),NOW)
            self.assertEqual(value['components']['freshness'],0)
            self.assertIn('observation_time_unusable',value['uncertainty_flags'])

    def test_declared_engine_or_generation_mismatch_not_used_as_peer(self):
        target=lead('target',model='Panda',condition='undamaged',generation='II',displacement_cc=1200,power_hp=69)
        rows=[dict(target,source_id='same',mileage_km=80001),
              dict(target,source_id='generation',generation='III'),
              dict(target,source_id='engine',displacement_cc=1400),
              dict(target,source_id='sport',power_hp=150),
              dict(target,source_id='unknown',generation=None,displacement_cc=None,power_hp=None)]
        for peers in (rows,PeerIndex(rows)):
            self.assertEqual({p['source_id'] for p in peers_for(target,peers)},{'same','unknown'})
