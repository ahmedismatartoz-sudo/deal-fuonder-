import copy
import os
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch
from test_core import NOW, row
from test_agents import envelope, cost, analyze
from deal_finder.models import Listing
from deal_finder.agents.professional import (POLICY, BODYWORK_LINES, COLLISION_CHECKS,
    AnnouncementAnomalyAgent, TechnicalRiskAgent, IndependentReviewAgent,
    conservative_economics, damage_severity)
from deal_finder.agents.repair_planning import execute as repair_plan
from deal_finder.technical_web import execute as research_risk


def doc(raw, **changes):
    listing=Listing.parse(raw['listing'])
    value=dict(source=listing.source,source_id=listing.source_id,observed_at=listing.observed_at,
               vehicle_id=listing.vehicle_id,verified=True,verified_by='primary-inspector',
               evidence_url='https://example.com/review',checked_at=NOW.isoformat())
    value.update(changes)
    return value


def reviewed(severe=False):
    raw=envelope(listing=row(99,price_eur=5000,condition='damaged' if severe else 'undamaged'),
                 operating_costs={name:cost(10000,10000) for name in ('transfer','transport','preparation','warranty','taxes','fees','contingency')})
    candidates=[Listing.parse(row(i,price_eur=20000 if severe else 10000)) for i in range(20 if severe else 12)]
    listing=Listing.parse(raw['listing'])
    specifications={key:getattr(listing,key) for key in ('make','model','generation','trim','fuel','transmission','year')}
    data=dict(acquisition=doc(raw,total_price_confirmed=True,available=True,documents_checked=True,
                             mileage_checked=True,purchase_price_cents=500000),
              damage_assessment=doc(raw,severity='severe' if severe else 'none'),
              model_risk_review=doc(raw,coverage_complete=True,specifications=specifications,
                  sources_consulted=['https://example.com/oem','https://example.com/recalls']),
              holding_plan=doc(raw,planned_days=90 if severe else 45,
                  daily_costs={key:dict(cost(0,0),unit='day',zero_basis='Synthetic documented zero for test')
                               for key in ('funding','storage','insurance')}),
              comparable_identities=[doc(raw,comparable_source=x.source,comparable_source_id=x.source_id,
                  comparable_observed_at=x.observed_at,comparable_vehicle_id=x.vehicle_id,
                  specifications_verified=True,specifications=specifications,price_cents=x.price_eur*100) for x in candidates])
    if severe:
        raw['inspection']['required_repairs']=['collision']
        raw['repair_quotes']=[dict(cost(180000,180000),repair_id='collision',vehicle_id=listing.vehicle_id,scope='parts_and_labor')]
        data['collision_checks']={key:doc(raw,passed=True,findings='Synthetic documented inspection result') for key in COLLISION_CHECKS}
        lines={key:cost(20000,20000) for key in BODYWORK_LINES}
        data['bodywork_quote']=dict(doc(raw),**cost(180000,180000),provider_type='external_bodyshop',
                                    provider_name='Synthetic Bodyshop',covered_repair_ids=['collision'],
                                    scope_complete=True,fixed_scope=True,lines=lines)
        data['repaired_history_comparables']=[doc(raw,comparable_vehicle_id='repaired-'+str(i),
            specifications=specifications,mileage_km=50000,seller_type='private',province='Milano',
            prior_damage_severity='severe',repair_history_disclosed=True,active=True,price_kind='total',
            price_cents=1800000) for i in range(8)]
        data['parts_plan']=[doc(raw,id='panel',repair_id='collision',quantity=1,part_number='TEST-PANEL',
            exact_fitment_confirmed=True,offers=[dict(doc(raw),**cost(30000,30000),seller='Synthetic Supplier',
                part_number='TEST-PANEL',tier='original',condition='new',available=True,fitment_confirmed=True,
                quantity_covered=1,shipping_included=True,deposit_included=True)])]
        data['labor_plan']=[doc(raw,id='collision-work',repair_id='collision',low_minutes=600,high_minutes=900,
                               method='Synthetic workshop time guide',consumables_included=True)]
        data['repair_plan_review']=doc(raw,parts_and_consumables_complete=True,labor_complete=True)
    raw['professional_evidence']=data
    initial=analyze(raw,candidates,NOW)['independent_review']
    economics=initial['economics']
    data['independent_review']=doc(raw,verified_by='second-inspector',passed=True,
        margin_low_cents=economics['margin_low_cents'],total_investment_cents=economics['total_investment_cents'],
        rationale='Synthetic independent verification of conservative calculations')
    return raw,candidates


class ProfessionalTests(unittest.TestCase):
    def test_complete_high_margin_candidate_still_cannot_bypass_uncalibrated_models(self):
        raw,candidates=reviewed()
        result=analyze(raw,candidates,NOW)
        review=result['independent_review']
        self.assertEqual(review['blocking_reasons'],[])
        self.assertTrue(review['approved_for_final_checks'])
        self.assertGreaterEqual(review['economics']['margin_low_cents'],200000)
        self.assertFalse(result['components']['supervisor']['data']['approved'])
        self.assertFalse(result['candidate_card']['high_opportunity_approved'])
        self.assertIsNone(result['components']['opportunity']['data']['forecast_profit_cents'])

    def test_2000_margin_is_hard_inclusive_threshold_after_all_costs(self):
        raw,candidates=reviewed()
        target=Listing.parse(row(99,price_eur=5050))
        analysis=analyze(raw,candidates,NOW)
        risk=analysis['technical_risk'];market=analysis['market']['data']
        repairs=analysis['repair'];opportunity=analysis['opportunity']
        economics=conservative_economics(raw,target,market,repairs,opportunity,risk,NOW)
        self.assertEqual(economics['margin_low_cents'],200000)
        self.assertTrue(economics['passes_margin'])
        self.assertEqual(economics['maximum_offer_cents'],505000)
        economics=conservative_economics(raw,Listing.parse(row(99,price_eur=5051)),market,repairs,opportunity,risk,NOW)
        self.assertFalse(economics['passes_margin'])

    def test_increasing_costs_or_lowering_resale_never_improves_opportunity(self):
        raw,candidates=reviewed();baseline=analyze(raw,candidates,NOW)['independent_review']['economics']
        raw['operating_costs']['transport']=cost(50000,50000)
        changed=analyze(raw,candidates,NOW)['independent_review']['economics']
        self.assertLess(changed['margin_low_cents'],baseline['margin_low_cents'])
        self.assertLess(changed['maximum_offer_cents'],baseline['maximum_offer_cents'])

    def test_missing_cost_never_turns_into_zero_or_a_maximum_offer(self):
        raw,candidates=reviewed();del raw['operating_costs']['warranty']
        value=analyze(raw,candidates,NOW)['independent_review']['economics']
        self.assertIsNone(value['margin_low_cents'])
        self.assertIsNone(value['maximum_offer_cents'])
        self.assertFalse(value['passes_margin'])

    def test_duplicates_and_reviewed_prices_cannot_inflate_comparable_count(self):
        raw,candidates=reviewed()
        raw['professional_evidence']['comparable_identities'][1]['comparable_vehicle_id']='vehicle-0'
        audit=analyze(raw,candidates,NOW)['independent_review']['comparable_audit']
        self.assertEqual(audit['accepted_count'],11)
        self.assertTrue(audit['blocking_reasons'])
        raw,candidates=reviewed();raw['professional_evidence']['comparable_identities'][0]['price_cents']=9999999
        self.assertEqual(analyze(raw,candidates,NOW)['independent_review']['comparable_audit']['accepted_count'],11)

    def test_other_snapshot_attestations_and_same_reviewer_are_rejected(self):
        raw,candidates=reviewed()
        raw['professional_evidence']['acquisition']['source_id']='other-car'
        self.assertFalse(analyze(raw,candidates,NOW)['independent_review']['approved_for_final_checks'])
        raw,candidates=reviewed();raw['professional_evidence']['independent_review']['verified_by']='inspector-test'
        self.assertFalse(analyze(raw,candidates,NOW)['independent_review']['approved_for_final_checks'])

    def test_conditional_financing_blocked_before_paid_research(self):
        raw,candidates=reviewed();raw['listing']['description']='Prezzo solo con finanziamento obbligatorio'
        with (patch('deal_finder.agents.workflow.identify_photos',side_effect=AssertionError('Do not call vision')),
              patch.dict(os.environ,DEAL_FINDER_PHOTO_IDENTITY_ENABLED='1')):
            value=analyze(raw,candidates,NOW)
        self.assertFalse(value['components']['market_selection']['data']['candidate'])
        self.assertEqual(value['repair']['status'],'blocked')
        self.assertIn('conditional_financing_price',value['announcement_anomalies']['data']['signals'])

    def test_optional_financing_not_a_conditional_price_signal(self):
        raw,candidates=reviewed();raw['listing']['description']='Finanziamento disponibile su richiesta'
        output=AnnouncementAnomalyAgent().execute(raw,Listing.parse(raw['listing']),candidates,NOW)
        self.assertNotIn('conditional_financing_price',output['data']['signals'])

    def test_budget_includes_20000_eur_and_rejects_above(self):
        raw,candidates=reviewed()
        analysis=analyze(raw,candidates,NOW)
        def economics(price):
            return conservative_economics(raw,Listing.parse(row(99,price_eur=price)),
                analysis['market']['data'],analysis['repair'],analysis['opportunity'],analysis['technical_risk'],NOW)
        out=economics(20000)
        self.assertTrue(out.get('budget_passed',False))
        self.assertEqual(out['minimum_margin_cents'],500000)
        out=economics(20001)
        self.assertFalse(out.get('budget_passed',False))

    def test_higher_purchase_requires_band_margin_in_every_scenario(self):
        raw,candidates=reviewed()
        analysis=analyze(raw,candidates,NOW)
        for price,required in ((6000,300000),(10000,400000),(15000,500000)):
            value=conservative_economics(raw,Listing.parse(row(99,price_eur=price)),
                analysis['market']['data'],analysis['repair'],analysis['opportunity'],analysis['technical_risk'],NOW)
            self.assertEqual(value['minimum_margin_cents'],required)
            for scenario in value['sensitivity_scenarios']:
                self.assertEqual(scenario['passes_margin'],scenario['margin_cents']>=required)

    def test_resale_uses_lowest_reviewed_asking_not_quartile(self):
        raw,candidates=reviewed()
        analysis=analyze(raw,candidates,NOW)
        market=dict(analysis['market']['data'],reviewed_comparables=[dict(price_eur=8000),dict(price_eur=10000)])
        value=conservative_economics(raw,Listing.parse(raw['listing']),market,
            analysis['repair'],analysis['opportunity'],analysis['technical_risk'],NOW)
        self.assertEqual(value['reference_cents'],800000)

    def test_severe_label_overrides_seller_undamaged_or_minor_claim(self):
        raw,candidates=reviewed();raw['listing']['description']='Auto gravemente incidentata, telaio piegato'
        value=damage_severity(raw,Listing.parse(raw['listing']),NOW)
        self.assertTrue(value['severe_controls_required'])
        self.assertEqual(value['severity'],'severe')

    def test_non_severe_negation_does_not_invent_collision(self):
        raw,candidates=reviewed();raw['listing']['description']='Non gravemente incidentata, mai incendiata'
        self.assertEqual(damage_severity(raw,Listing.parse(raw['listing']),NOW)['severity'],'none')

    def test_unknown_damage_requires_severe_controls_until_reviewed(self):
        raw=envelope(listing=row(99,condition='damaged'))
        value=damage_severity(raw,Listing.parse(raw['listing']),NOW)
        self.assertTrue(value['severe_controls_required'])
        self.assertEqual(value['severity'],'unknown')

    def test_fully_documented_severe_case_is_reviewable_with_stricter_buffers(self):
        raw,candidates=reviewed(True);value=analyze(raw,candidates,NOW)['independent_review']
        self.assertEqual(value['blocking_reasons'],[])
        self.assertTrue(value['approved_for_final_checks'])
        economics=value['economics']
        self.assertEqual(economics['reference_cents'],1800000)
        self.assertEqual(economics['sale_stress_cents'],270000)
        self.assertEqual(economics['repair_stress_cents'],72000)
        self.assertGreaterEqual(economics['reserve_cents'],200000)
        self.assertFalse(value['final_opportunity_approved'])

    def test_severe_case_never_uses_only_healthy_resale_prices(self):
        raw,candidates=reviewed(True);raw['professional_evidence']['repaired_history_comparables']=[]
        value=analyze(raw,candidates,NOW)['independent_review']
        self.assertIsNone(value['economics']['margin_low_cents'])
        self.assertFalse(value['approved_for_final_checks'])

    def test_every_severe_collision_check_is_mandatory(self):
        for key in COLLISION_CHECKS:
            with self.subTest(key=key):
                raw,candidates=reviewed(True);del raw['professional_evidence']['collision_checks'][key]
                self.assertFalse(analyze(raw,candidates,NOW)['independent_review']['approved_for_final_checks'])

    def test_external_bodyshop_required_and_total_reconciled(self):
        raw,candidates=reviewed(True);raw['professional_evidence']['bodywork_quote']['provider_type']='internal_mechanic'
        self.assertFalse(analyze(raw,candidates,NOW)['independent_review']['approved_for_final_checks'])
        raw,candidates=reviewed(True);raw['professional_evidence']['bodywork_quote']['high_cents']=100
        self.assertIsNone(analyze(raw,candidates,NOW)['independent_review']['economics']['margin_low_cents'])

    def test_missing_bodywork_line_or_understated_quote_cannot_pass(self):
        raw,candidates=reviewed(True);del raw['professional_evidence']['bodywork_quote']['lines']['adas_calibration']
        self.assertFalse(analyze(raw,candidates,NOW)['independent_review']['approved_for_final_checks'])
        raw,candidates=reviewed(True);raw['repair_quotes'][0].update(low_cents=100000,high_cents=100000)
        value=analyze(raw,candidates,NOW)['independent_review']
        self.assertIn('Repair quotes understate specialist bodyshop cost',value['blocking_reasons'])
        self.assertEqual(value['economics']['repair_cost_high_cents'],180000)

    def test_fake_risk_report_or_wrong_variant_cannot_clear_risk(self):
        raw,candidates=reviewed()
        raw['professional_evidence']['model_risks']=[doc(raw,specifications={'model':'other'},
            finding='Engine issue',required_check='inspect',vehicle_check_passed=True)]
        self.assertFalse(analyze(raw,candidates,NOW)['independent_review']['approved_for_final_checks'])

    def test_coordinator_does_not_skip_missing_earlier_evidence(self):
        raw,candidates=reviewed();del raw['professional_evidence']['acquisition']
        flow=analyze(raw,candidates,NOW)['coordinator']
        self.assertEqual(flow['next_ready_tasks'],['acquisition_and_anomalies'])
        self.assertEqual(flow['tasks'][1]['status'],'blocked')
        self.assertFalse(flow['automatic_paid_calls'])

    def test_parts_shipping_fitment_and_availability_mandatory(self):
        for key in ('shipping_included','fitment_confirmed','available'):
            with self.subTest(key=key):
                raw,candidates=reviewed(True);raw['professional_evidence']['parts_plan'][0]['offers'][0][key]=False
                self.assertNotEqual(repair_plan(raw,Listing.parse(raw['listing']),NOW)['status'],'completed')

    def test_shared_disassembly_requires_evidenced_overlap(self):
        raw,candidates=reviewed(True);data=raw['professional_evidence']
        first=data['labor_plan'][0];first['shared_disassembly_group']='front'
        second=dict(first,id='second',low_minutes=120,high_minutes=180)
        data['labor_plan'].append(second)
        self.assertIsNone(repair_plan(raw,Listing.parse(raw['listing']),NOW)['data']['effective_minutes'])
        data['labor_overlap']=[doc(raw,group='front',minutes=120,operation_ids=['collision-work','second'])]
        self.assertEqual(repair_plan(raw,Listing.parse(raw['listing']),NOW)['data']['effective_minutes'],960)
        data['labor_overlap'][0]['minutes']=9999
        self.assertIsNone(repair_plan(raw,Listing.parse(raw['listing']),NOW)['data']['effective_minutes'])

    def test_raw_secrets_or_fake_professional_fields_are_quarantined(self):
        from deal_finder.queue import Queue
        queue=Queue(':memory:')
        try:
            value=queue.submit('invalid',[dict(envelope(),professional_evidence='bad')])
            self.assertEqual(value['quarantined_at_intake'],1)
            self.assertEqual(queue.work_one()['state'],'done')
        finally:queue.close()

    def test_severe_source_fields_survive_normalization_and_exclude_healthy_comparisons(self):
        from deal_finder.pricing import estimate
        items=[Listing.parse(row(i)) for i in range(12)]
        items[0]=Listing.parse(row(0,damage_severity='severe',damage_indicators=['structural']))
        self.assertEqual(estimate(Listing.parse(row(99)),items,as_of=NOW)['comparable_count'],11)
        value=damage_severity(envelope(),items[0],NOW)
        self.assertTrue(value['severe_controls_required'])

    def test_publication_recomputes_even_when_old_supervisor_and_profit_are_forged(self):
        from deal_finder.queue import Queue
        from deal_finder.archive import Archive
        from deal_finder.publication import PublicationAgent
        from test_archive import page,event
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,'publication.db');queue=Queue(path);archive=Archive(path)
            try:
                raw,candidates=reviewed();raw['listing']['price_eur']=9000
                archive.ingest(page([event('99',payload=raw['listing'],observed_at=raw['listing']['observed_at'])],source='manual'),as_of=NOW)
                queue.submit('forged-publication',[raw]);job=queue.claim()
                outputs=analyze(raw,candidates,NOW)
                outputs['components']['supervisor']['data'].update(approved=True,checks={'all':True})
                outputs['components']['publication']['data']['publishable']=True
                outputs['components']['opportunity']['data'].update(forecast_profit_cents=9999999,forecast_profit_low_cents=9999999)
                queue.finish(job,NOW,candidates,outputs)
                value=PublicationAgent().preview(queue,archive,'forged-publication',as_of=NOW)
                self.assertEqual(value['items'],[])
                self.assertIn('Conservative margin below 3000 EUR',value['rejected'][0]['reasons'])
            finally:queue.close();archive.close()

    def test_history_preserves_ribassi_removal_and_shared_photo_uncertainty(self):
        from deal_finder.queue import Queue
        from deal_finder.archive import Archive
        from deal_finder.agents.collection_audit import execute
        from test_archive import page,event
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,'history.db');queue=Queue(path);archive=Archive(path)
            try:
                archive.ingest(page([event('99',payload=dict(row(99),price_kind='total'))],source='manual'),as_of=NOW)
                later=NOW+timedelta(seconds=1)
                archive.ingest(page([event('99',payload=dict(row(99,price_eur=9000),price_kind='total'),
                    observed_at=later.isoformat(),active=False)],run='removed',source='manual'),as_of=later)
                output=execute(queue.db,Listing.parse(row(99)),[],later)
                self.assertEqual(output['price_changes'][0]['delta_eur'],-1000)
                self.assertFalse(output['history'][-1]['active'])
                self.assertFalse(output['removal_proves_sale'])
            finally:queue.close();archive.close()

    def test_professional_filters_do_not_replace_missing_margin_with_zero(self):
        from deal_finder.agents.handoff import filter_cards
        cards=[dict(conservative_margin_low_cents=None),dict(conservative_margin_low_cents=199999),
               dict(conservative_margin_low_cents=200000)]
        self.assertEqual(filter_cards(cards,min_conservative_margin_low_cents=200000),[cards[2]])

    def test_outcome_learning_counts_cost_underestimation_without_changing_policy(self):
        from test_agents import EvaluationTests
        from deal_finder.agents.evaluation import EvaluationAgent
        records=[dict(EvaluationTests().sample(i),predicted_repair_high_cents=50000,
                      actual_repair_cents=100000,repair_evidence_url='https://example.com/invoice') for i in range(30)]
        value=EvaluationAgent().execute(records,model_version='sale-v1',training_vehicle_ids=[],as_of=NOW)
        learning=value.data['learning_review']
        self.assertEqual(learning['repair_sample_count'],30)
        self.assertEqual(learning['mean_repair_underestimate_cents'],50000)
        self.assertFalse(learning['automatic_policy_changes'])

    def test_missing_holding_costs_block_even_an_apparently_high_margin(self):
        raw,candidates=reviewed();del raw['professional_evidence']['holding_plan']
        review=analyze(raw,candidates,NOW)['independent_review']
        self.assertFalse(review['approved_for_final_checks'])
        self.assertIsNone(review['economics']['margin_low_cents'])
        self.assertIsNone(review['economics']['maximum_offer_cents'])

    def test_zero_holding_rate_requires_an_explanation_and_a_full_horizon(self):
        for change in ('missing_basis','short_horizon','missing_insurance','wrong_unit'):
            with self.subTest(change=change):
                raw,candidates=reviewed();plan=raw['professional_evidence']['holding_plan']
                if change=='missing_basis':del plan['daily_costs']['funding']['zero_basis']
                if change=='short_horizon':plan['planned_days']=44
                if change=='missing_insurance':del plan['daily_costs']['insurance']
                if change=='wrong_unit':plan['daily_costs']['storage']['unit']='month'
                self.assertIsNone(analyze(raw,candidates,NOW)['independent_review']['economics']['margin_low_cents'])

    def test_holding_costs_reduce_margin_and_maximum_offer_without_disabling_stress(self):
        raw,candidates=reviewed();base=analyze(raw,candidates,NOW)['independent_review']['economics']
        raw['professional_evidence']['holding_plan']['daily_costs']['storage']=dict(cost(1000,1000),unit='day')
        value=analyze(raw,candidates,NOW)['independent_review']['economics']
        self.assertEqual(value['holding']['high_cents'],45000)
        self.assertEqual(value['margin_low_cents'],base['margin_low_cents']-45000)
        self.assertEqual(value['maximum_offer_cents'],base['maximum_offer_cents']-45000)
        self.assertFalse(value['passes_margin'])

    def test_base_margin_alone_cannot_pass_when_simultaneous_stress_fails(self):
        raw,candidates=reviewed();raw['listing']['price_eur']=5300
        value=analyze(raw,candidates,NOW)['independent_review']['economics']
        self.assertGreaterEqual(value['sensitivity_scenarios'][0]['margin_cents'],200000)
        self.assertLess(value['margin_low_cents'],200000)
        self.assertFalse(value['passes_resilience'])
        self.assertEqual(value['sensitivity_scenarios'][-1]['name'],'combined_adverse')

    def test_severe_holding_horizon_cannot_use_ordinary_45_days(self):
        raw,candidates=reviewed(True);raw['professional_evidence']['holding_plan']['planned_days']=45
        value=analyze(raw,candidates,NOW)['independent_review']
        self.assertIsNone(value['economics']['margin_low_cents'])
        self.assertFalse(value['approved_for_final_checks'])


class RiskResearchTests(unittest.TestCase):
    def test_research_is_opt_in_and_cannot_clear_a_vehicle(self):
        with patch.dict(os.environ,DEAL_FINDER_TECHNICAL_RISK_WEB_ENABLED=''):
            value=research_risk(Listing.parse(row()),{},NOW)
        self.assertEqual(value['status'],'configuration_required')
        self.assertFalse(value['verified_on_vehicle'])

    def test_unvisited_url_rejected_even_if_provider_claims_a_risk(self):
        adapter=lambda _:dict(status='researched',web_search_performed=True,
            visited_urls=['https://example.com/actual'],findings=dict(findings=[dict(finding='issue',
                applicability='this variant',required_check='inspect',source_urls=['https://example.com/invented'],uncertainties=[])],missing_evidence=[]))
        value=research_risk(Listing.parse(row()),{},NOW,adapter=adapter)
        self.assertEqual(value['findings'],[])
        self.assertEqual(len(value['rejected_claims']),1)
        self.assertFalse(value['verified_on_vehicle'])

    def test_cited_finding_is_only_a_hypothesis_for_inspection(self):
        adapter=lambda _:dict(status='researched',web_search_performed=True,
            visited_urls=['https://example.com/oem'],findings=dict(findings=[dict(finding='issue',
                applicability='this variant',required_check='inspect',source_urls=['https://example.com/oem'],uncertainties=[])],missing_evidence=[]))
        value=research_risk(Listing.parse(row()),{},NOW,adapter=adapter)
        self.assertEqual(value['status'],'provisional')
        self.assertTrue(value['operator_review_required'])
        self.assertFalse(value['findings'][0]['verified_on_vehicle'])
