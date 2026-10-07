import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from deal_finder.facebook_evidence import recover
from deal_finder.facebook_opportunities import build_report, compatible, FacebookScreening, PREFIX
from deal_finder.vehicle_searches import title_identity

NOW=datetime.now(timezone.utc)


def car(identifier='target',price=2000,**changes):
    p=dict(source='facebook_marketplace',source_id=identifier,active=True,
        observed_at=(NOW-timedelta(hours=1)).isoformat(),url='https://example.com/'+identifier,
        make='Fiat',model='Panda',title='2018 Fiat Panda 1.2',year=2018,mileage_km=80000,
        fuel='petrol',transmission='manual',price_eur=price,price_kind='total',
        displacement_cc=1242,condition='undamaged',city='Milano',description='')
    p.update(changes)
    return p


def peers():
    return [car('peer'+str(i),10000+i*500,source='export',mileage_km=80001+i) for i in range(3)]


class FacebookEvidenceTests(unittest.TestCase):
    def test_provider_miles_never_become_km_or_clean_total(self):
        p=recover(dict(title='2018 Fiat Panda',description='',original={'car_miles':80000,'condition':'USED'},price_kind='unknown'))
        self.assertEqual(p['year'],2018)
        self.assertIsNone(p['mileage_km'])
        self.assertFalse(p['facebook_evidence']['registration_year_verified'])
        self.assertFalse(p['facebook_evidence']['total_price_verified'])
        self.assertNotEqual(p.get('condition'),'undamaged')
        self.assertEqual(p['price_kind'],'unknown')

    def test_explicit_prefix_suffix_and_mila_km_are_recovered(self):
        for text in ('KM 170.000','170 000 chilometri','170mila km','170k km'):
            with self.subTest(text=text):
                p=recover(dict(title='2018 Fiat Panda',description=text))
                self.assertEqual(p['mileage_km'],170000)

    def test_servicing_milestone_does_not_replace_current_odometer(self):
        p=recover(dict(title='2018 Fiat Panda',description='Distribuzione fatta a 70.000 km.\nAuto con 90.000 km.'))
        self.assertEqual(p['mileage_km'],90000)

    def test_conflicting_mileage_is_not_selected(self):
        p=recover(dict(title='2018 Fiat Panda',description='km 90.000, chilometri 120.000'))
        self.assertIsNone(p['mileage_km'])
        self.assertTrue(p['facebook_evidence']['conflicts'])

    def test_spaced_fiat_suffix_corrects_existing_archive_family(self):
        self.assertEqual(title_identity('2015 Fiat 500 L')['model'],'500L')
        self.assertEqual(title_identity('2016 Fiat 500 X')['model'],'500X')
        original=dict(title='2015 Fiat 500 L',model='500',make='Fiat',description='1.6 diesel')
        p=recover(original)
        self.assertEqual(p['model'],'500L')
        self.assertEqual(original['model'],'500')
        self.assertFalse(p['facebook_evidence']['conflicts'])

    def test_fuel_negation_and_dual_fuel_remain_separate(self):
        self.assertEqual(recover(dict(title='2018 Fiat Panda',description='benzina e GPL'))['fuel'],'petrol_lpg')
        p=recover(dict(title='2018 Fiat Panda',description='benzina, non diesel'))
        self.assertEqual(p['fuel'],'petrol')
        p=recover(dict(title='2018 Fiat Panda',description='benzina oppure diesel'))
        self.assertIsNone(p['fuel'])
        self.assertTrue(p['facebook_evidence']['conflicts'])


class FacebookRankingTests(unittest.TestCase):
    def test_pessimistic_low_reference_never_becomes_unknown_cost_net(self):
        r=build_report([car()],peers(),NOW)
        self.assertEqual(r['total_research_candidates'],1)
        c=r['candidates'][0]
        self.assertEqual(c['conservative_asking_exit_scenario_eur'],8500)
        self.assertEqual(c['maximum_margin_before_unknown_costs_eur'],5750)
        self.assertIsNone(c['net_margin_estimate_eur'])
        self.assertEqual(r['opportunities'],[])
        self.assertEqual(r['shortage'],50)

    def test_unknown_provider_units_keep_both_hypotheses_and_null_km(self):
        p=car(mileage_km=None,facebook_evidence={'raw_provider_car_miles':80000})
        r=build_report([p],peers(),NOW)
        c=r['candidates'][0]
        self.assertIsNone(c['mileage_km'])
        self.assertEqual(c['research_mileage_hypotheses_km'],[80000,128747])
        self.assertIn('provider_mileage_units_unresolved',c['blocking_reasons'])
        self.assertEqual(r['opportunities'],[])

    def test_only_complete_independent_cost_review_can_enter_final_list(self):
        p=car()
        output={'independent_review':{'approved_for_final_checks':True,'economics':{'margin_low_cents':250000}}}
        r=build_report([p],peers(),NOW,reviews={(p['source_id'],p['observed_at']):output})
        self.assertEqual(r['opportunities'][0]['net_margin_estimate_eur'],2500)
        self.assertFalse(r['opportunities'][0]['buy_recommendation'])

    def test_actual_five_thousand_boundary_applies_even_to_old_review(self):
        p=car(price=5000)
        output={'independent_review':{'approved_for_final_checks':True,'economics':{'margin_low_cents':250000}}}
        r=build_report([p],peers(),NOW,reviews={(p['source_id'],p['observed_at']):output})
        self.assertEqual(r['opportunities'],[])
        self.assertEqual(r['candidates'],[])
        self.assertEqual(r['exclusions']['insufficient_pessimistic_headroom'],1)

    def test_bad_price_terms_stale_damage_and_distinct_peer_requirement(self):
        for bad in (dict(description='Prezzo con finanziamento obbligatorio'),dict(active=False),
                    dict(observed_at=(NOW-timedelta(days=31)).isoformat()),dict(description='Airbag esplosi')):
            with self.subTest(bad=bad):
                self.assertEqual(build_report([car(**bad)],peers(),NOW)['candidates'],[])
        p=peers()[0]
        self.assertEqual(build_report([car()],[p,dict(p,source_id='repost'),peers()[1]],NOW)['candidates'],[])

    def test_incompatible_model_engine_and_performance_do_not_raise_value(self):
        for change in (dict(model='500L'),dict(displacement_cc=1598),dict(fuel='diesel'),
                       dict(title='2018 Fiat Panda Abarth'),dict(year=2021),dict(mileage_km=200000)):
            with self.subTest(change=change):
                self.assertFalse(compatible(car(),car('other',source='export',**change)))
        self.assertTrue(compatible(car(make='BMW',model='Serie 1'),
                                  car('other',source='export',make='bmw',model='118')))

    def test_fifty_is_a_cap_not_a_filled_quota(self):
        all_rows=[car(str(i),price=1000+i) for i in range(70)]
        r=build_report(all_rows,peers(),NOW)
        self.assertEqual(r['total_research_candidates'],70)
        self.assertEqual(len(r['candidates']),50)
        self.assertEqual(r['candidates'][0]['source_id'],'0')
        self.assertEqual(r['opportunities'],[])

    def test_severe_photo_result_removes_candidate(self):
        p=car()
        output={'enrichment':{'photo_damage_assessment':{'possible_severe_damage':True}}}
        r=build_report([p],peers(),NOW,reviews={(p['source_id'],p['observed_at']):output})
        self.assertEqual(r['candidates'],[])
        self.assertEqual(r['exclusions']['possible_severe_damage_in_photos'],1)

    def test_review_of_different_snapshot_never_supplies_net(self):
        p=car()
        output={'independent_review':{'approved_for_final_checks':True,'economics':{'margin_low_cents':250000}}}
        r=build_report([p],peers(),NOW,reviews={(p['source_id'],NOW.isoformat()):output})
        self.assertEqual(r['opportunities'],[])


class FacebookPersistenceTests(unittest.TestCase):
    def test_full_latest_archive_scan_restart_and_optin_budget(self):
        from deal_finder.archive import Archive
        from deal_finder.price_memory import PriceMemory
        from deal_finder.queue import Queue
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'DEAL_FINDER_FACEBOOK_RESEARCH_LIMIT':'2'}):
            path=os.path.join(tmp,'test.db')
            archive=Archive(path)
            def ingest(source,rows,run):
                archive.ingest(dict(source=source,run_id=run,page_id='p',mode='initial',scope={'country':'IT'},
                    input_cursor=None,next_cursor=None,complete=True,records=[dict(source_id=p['source_id'],
                        observed_at=p['observed_at'],url=p['url'],active=p['active'],payload=p) for p in rows]))
            # Target entries span more than one 50-row read page.
            ingest('facebook_marketplace',[car(str(i),description='80000 km benzina') for i in range(110)],'fb')
            ingest('export',peers(),'market')
            memory=PriceMemory(archive.db)
            while memory.sync(NOW,recover_identity=False): pass
            archive.close()
            result=FacebookScreening(path).step()
            self.assertEqual(result['facebook_ads_examined'],110)
            self.assertEqual(result['research_observations_queued_total'],2)
            restarted=FacebookScreening(path).step()
            self.assertEqual(restarted['facebook_screening'],'already_completed')
            queue=Queue(path)
            self.assertEqual(queue.db.execute('SELECT count(*) FROM batches WHERE batch_id LIKE ?',(PREFIX+'%',)).fetchone()[0],2)
            self.assertEqual(queue.db.execute('SELECT count(*) FROM raw_records WHERE quality_issue IS NOT NULL').fetchone()[0],0)
            queue.close()

    def test_report_route_is_private_and_separate_from_generic_shortlist(self):
        try:
            from fastapi.testclient import TestClient
            from deal_finder.api import app
        except ImportError:
            self.skipTest('API dependencies required in CI')
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'DEAL_FINDER_DB':os.path.join(tmp,'api.db'),
                    'DEAL_FINDER_API_TOKEN':'test-token'}),TestClient(app) as client:
            self.assertEqual(client.get('/facebook/opportunities/latest').status_code,401)
            self.assertEqual(client.get('/facebook/opportunities/latest',headers={'authorization':'Bearer test-token'}).status_code,404)
