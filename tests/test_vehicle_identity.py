import unittest
from deal_finder.vehicle_identity import resolve,enrich
from deal_finder.identity_evaluation import evaluate
from deal_finder.price_memory import same_variant
from test_core import NOW


class VehicleIdentityTests(unittest.TestCase):
    def test_recovers_original_fields_and_distinguishes_normalizer_loss(self):
        p=dict(original=dict(vehicle=dict(rawPowerInHp=150,rawPowerInKw=110,rawCylinderCapacity=1995,
            transmissionType='Cambio automatico',driveTrain='Posteriore',bodyType='Berlina',
            motorTypeName='2.0 Diesel',engineCode='B47',fuelCategory=dict(formatted='Diesel'))))
        dossier=resolve(p,source_url='https://example.com/car')
        for field,value in dict(power_hp=150,power_kw=110,displacement_cc=1995,
              transmission='automatic',drivetrain='rwd',body_type='sedan',engine_code='b47').items():
            self.assertEqual(dossier['fields'][field]['value'],value)
            self.assertEqual(dossier['fields'][field]['recovery_status'],'normalization_gap')
            self.assertEqual(dossier['fields'][field]['claims'][0]['source_url'],'https://example.com/car')
        self.assertEqual(dossier['fields']['generation']['recovery_status'],'absent_from_source')
        self.assertFalse(dossier['physical_identity_verified'])

    def test_structured_vs_description_conflict_blocks_comparisons(self):
        p=enrich(dict(version_text='118d',power_hp=150,description='Motore diesel 143 CV',
                      original=dict(vehicle=dict(rawPowerInHp=150))))
        self.assertEqual(p['identity_dossier']['conflicts'][0]['field'],'power_hp')
        self.assertFalse(p['identity_dossier']['usable_for_price_comparison'])
        self.assertFalse(same_variant(p,dict(p)))
        self.assertEqual(enrich(p)['identity_dossier']['conflicts'],p['identity_dossier']['conflicts'])

    def test_power_unit_rounding_does_not_invent_conflict_but_real_mismatch_does(self):
        self.assertFalse(resolve(dict(power_hp=116,power_kw=85,version_text='115cv'))['conflicts'])
        self.assertTrue(resolve(dict(power_hp=150,power_kw=85))['conflicts'])

    def test_normalizes_aliases_preserves_engine_and_drive_differences(self):
        a=enrich(dict(version_text='1.5 Diesel',transmission='Cambio automatico',drivetrain='Posteriore',engine_code='B47'))
        b=enrich(dict(version_text='1.5 Diesel',transmission='automatico',drivetrain='rwd',engine_code='B47'))
        self.assertTrue(same_variant(a,b))
        self.assertFalse(same_variant(a,dict(b,engine_code='N47')))
        self.assertFalse(same_variant(a,dict(b,drivetrain='awd')))

    def test_explicit_generation_and_trim_are_seller_claims_not_verified(self):
        p=enrich(dict(version_text='Panda III 2015 0.9 natural power Lounge 80cv'))
        self.assertEqual(p['generation'],'iii');self.assertEqual(p['trim'],'lounge')
        self.assertEqual(p['identity_dossier']['fields']['trim']['status'],'declared')
        self.assertFalse(p['identity_dossier']['physical_identity_verified'])
        self.assertNotIn('engine_code',p)

    def test_source_provenance_survives_listing_handoff(self):
        p=enrich(dict(original=dict(vehicle=dict(rawPowerInHp=69,driveTrain='Anteriore'))))
        p.pop('original')
        dossier=enrich(p)['identity_dossier']
        self.assertEqual(dossier['fields']['drivetrain']['claims'][0]['source_path'],'original.vehicle.driveTrain')

    def labelled(self,i,**changes):
        listing=dict(source='synthetic',source_id=str(i),observed_at=NOW.isoformat(),
                     url='https://example.com/fixture',transmission='Cambio automatico',power_hp=150)
        listing.update(changes)
        return dict(vehicle_id='fixture-'+str(i),listing=listing,
            reviewed_labels=dict(verified=True,verified_by='synthetic-independent-reviewer',
                origin='vehicle_document',source='synthetic',source_id=str(i),observed_at=NOW.isoformat(),
                evidence_url='https://example.com/synthetic-document',checked_at=NOW.isoformat(),
                fields=dict(transmission='automatic',power_hp=150)))

    def test_separate_synthetic_holdout_measures_errors_and_abstentions(self):
        # Development fixtures 0..5; challenge fixtures 6..11 are separate identities.
        records=[self.labelled(i) for i in range(6,12)]
        records[0]['listing']['power_hp']=143
        records[1]['listing']['description']='Motore 143 CV'
        result=evaluate(records,['fixture-'+str(i) for i in range(6)],as_of=NOW)
        self.assertEqual(result['holdout_vehicles'],6)
        self.assertEqual(result['fields']['power_hp']['incorrect'],1)
        self.assertEqual(result['fields']['power_hp']['abstained'],1)
        self.assertEqual(result['fields']['transmission']['correct'],6)
        self.assertFalse(result['physical_identity_release_approved'])

    def test_rejects_training_overlap_duplicates_and_seller_labels(self):
        good=self.labelled(6);bad=self.labelled(7);bad['reviewed_labels']['origin']='seller_text'
        result=evaluate([self.labelled(0),good,good,bad],['fixture-0'],as_of=NOW)
        self.assertEqual(result['holdout_vehicles'],1);self.assertEqual(len(result['rejected']),3)

    def test_fractional_kw_and_charger_power_are_not_lost_or_mixed(self):
        p=resolve(dict(power_kw=110.3,power_hp=150,description='Ricarica a 50 kW'))
        self.assertEqual(p['values']['power_kw'],110.3)
        self.assertFalse(p['conflicts'])

    def test_full_workflow_blocks_source_conflict_before_paid_identity_research(self):
        from test_agents import envelope,analyze
        from test_core import row
        from deal_finder.models import Listing
        from unittest.mock import patch
        raw=envelope(listing=row(99,price_eur=2000,power_hp=150,description='Motore 143 CV'))
        candidates=[Listing.parse(row(i,price_eur=15000,power_hp=150)) for i in range(12)]
        with patch('deal_finder.agents.workflow.identify_photos',side_effect=AssertionError('No paid call for conflict')):
            result=analyze(raw,candidates,NOW)
        self.assertEqual(result['identity']['status'],'needs_review')
        self.assertFalse(result['components']['market_selection']['data']['candidate'])
        self.assertFalse(result['components']['publication']['data']['publishable'])

    def test_document_verifies_only_bound_reviewed_fields_and_never_clears_conflict(self):
        from deal_finder.vehicle_identity import annotate_verified_fields
        from deal_finder.models import Listing
        from test_core import row
        listing=Listing.parse(row(99,power_hp=150))
        dossier=resolve(listing.to_dict())
        proof=dict(verified=True,vehicle_id=listing.vehicle_id,verified_by='reviewer',
            evidence_url='https://example.com/document',verified_at=NOW.isoformat(),
            source=listing.source,source_id=listing.source_id,observed_at=listing.observed_at,
            evidence_origin='vehicle_document',verified_fields=dict(power_hp=150,transmission='manual'))
        result=annotate_verified_fields(dossier,proof,listing,NOW)
        self.assertEqual(result['fields']['power_hp']['status'],'verified')
        self.assertEqual(result['fields']['fuel']['status'],'declared')
        self.assertFalse(result['exact_variant_verified'])
        mismatched=dict(proof,source_id='other')
        self.assertFalse(annotate_verified_fields(dossier,mismatched,listing,NOW)['physical_identity_verified'])
        conflict=resolve(dict(listing.to_dict(),description='Motore 143 CV'))
        self.assertEqual(annotate_verified_fields(conflict,proof,listing,NOW)['fields']['power_hp']['status'],'conflict')


class SourceAliasTests(unittest.TestCase):
    def test_fuel_components_and_octane_are_not_engine_conflicts(self):
        for category,component,expected in [('GPL','Gas di petrolio liquefatto','lpg'),('Metano','Biogas','cng'),('Benzina','Benzina E10 91','petrol'),('Benzina/Metano','Benzina','petrol_cng')]:
            d=resolve(dict(original=dict(vehicle=dict(fuelCategory=dict(formatted=category),primaryFuel=dict(formatted=component)))))
            self.assertFalse(d['conflicts'])
            self.assertEqual(d['fields']['fuel']['value'],expected)
            self.assertEqual(d['fields']['fuel']['source_components'][0]['raw_value'],component)

    def test_generic_automatic_description_preserves_structured_subtype(self):
        d=resolve(dict(description='Cambio automatico',original=dict(vehicle=dict(transmissionType='Semiautomatico'))))
        self.assertFalse(d['conflicts']);self.assertEqual(d['fields']['transmission']['value'],'semi_automatic')
        self.assertTrue(resolve(dict(description='Cambio manuale',original=dict(vehicle=dict(transmissionType='Semiautomatico'))))['conflicts'])

    def test_tax_horsepower_is_not_engine_power(self):
        self.assertFalse(resolve(dict(power_hp=150,description='17 CV fiscali. Motore 150 CV'))['conflicts'])

    def test_good_condition_is_declared_and_never_inspected(self):
        from deal_finder.damage_screening import classify
        d=classify(dict(description='Auto in ottime condizioni'))
        self.assertEqual(d['category'],'clean');self.assertFalse(d['severity_verified'])
        self.assertEqual(classify(dict(description='Gomme in ottime condizioni'))['category'],'unknown')
        self.assertEqual(classify(dict(description='Auto in ottime condizioni, motore fuso'))['category'],'severe')
