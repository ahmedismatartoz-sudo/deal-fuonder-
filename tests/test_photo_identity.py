import unittest
from unittest.mock import patch
from test_core import row, NOW
from deal_finder.agents.photo_identity import execute, plan, public_url, research


def finding(field, value, origin='photo', urls=None, indexes=None):
    return dict(field=field,value=value,origin=origin,urls=urls or [],photo_indexes=[0] if indexes is None else indexes)


def response(claims):
    return dict(status='researched',web_search_performed=True,visited_urls=['https://fiat.com/panda'],
                findings=dict(claims=claims,alternatives=[],visual_clues=['shape and badge'],missing_evidence=[]))


def raw(): return dict(listing=row(99,image_urls=['https://example.com/front.jpg']))


class PhotoTests(unittest.TestCase):
    def test_photo_ad_web_still_provisional(self):
        out=execute(raw(),NOW,lambda _:response([finding('make','fiat'),finding('model','panda')]))
        self.assertEqual(out['status'],'provisional_identification')
        self.assertFalse(out['identity_attestation'])
        self.assertFalse(out['exact_part_fitment_confirmed'])
        self.assertIn('engine_code',out['missing_fields'])
    def test_photo_technical_fields_not_guessed(self):
        out=execute(raw(),NOW,lambda _:response([finding('engine_code','169A4.000')]))
        self.assertNotIn('engine_code',out['proposed_specs'])
        self.assertEqual(len(out['rejected_claims']),1)
    def test_contradictory_model_needs_review(self):
        out=execute(raw(),NOW,lambda _:response([finding('model','500')]))
        self.assertEqual(out['status'],'needs_review')
        self.assertNotIn('model',out['proposed_specs'])
    def test_unvisited_web_sources_rejected(self):
        out=execute(raw(),NOW,lambda _:response([finding('engine_code','169',origin='web',urls=['https://invented.com'])]))
        self.assertEqual(len(out['rejected_claims']),1)
    def test_invalid_image_index_rejected(self):
        out=execute(raw(),NOW,lambda _:response([finding('model','panda',indexes=[9])]))
        self.assertEqual(len(out['rejected_claims']),1)
    def test_unknown_model_not_promoted(self):
        out=execute(raw(),NOW,lambda _:response([finding('generation','unknown')]))
        self.assertEqual(out['status'],'needs_review')
    def test_absent_claim_is_rejected_without_losing_other_evidence(self):
        out=execute(raw(),NOW,lambda _:response([finding('engine_code','169',origin='listing'),finding('model','panda')]))
        self.assertEqual(len(out['accepted_claims']),1)
        self.assertEqual(len(out['rejected_claims']),1)
    def test_no_photos_no_call_and_provider_not_configured(self):
        value=raw();value['listing']['image_urls']=[]
        self.assertEqual(execute(value,NOW,lambda _:self.fail())['status'],'needs_evidence')
        with patch.dict('os.environ',DEAL_FINDER_PHOTO_IDENTITY_ENABLED=''):
            self.assertEqual(research(plan(raw()))['status'],'configuration_required')

    def test_bad_photo_isolated_from_other_agents(self):
        value=raw();value['listing']['image_urls']=['http://example.com/photo.jpg']
        self.assertEqual(execute(value,NOW,lambda _:self.fail())['status'],'needs_review')
    def test_input_and_output_bounds(self):
        value=raw();value['listing']['description']='a'*17000
        self.assertTrue(plan(value)['description_truncated'])
        self.assertEqual(len(plan(value)['description']),16000)
        value['listing']['make']={}
        with self.assertRaises(ValueError): plan(value)
        value=response([finding('make','fiat')]*101)
        self.assertEqual(execute(raw(),NOW,lambda _:value)['status'],'needs_review')

    def test_photo_conflict_blocks_parts_handoff(self):
        from test_agents import analyze, envelope, pool
        conflict=dict(status='needs_review', conflicting_fields=[dict(field='model',values=['panda','500'])])
        with patch.dict('os.environ',DEAL_FINDER_PHOTO_IDENTITY_ENABLED='1'):
            with patch('deal_finder.agents.workflow.identify_photos',return_value=conflict):
                value=analyze(envelope(),pool(),NOW)
        self.assertEqual(value['handoff']['route'],'enrichment')
        self.assertEqual(value['parts_research']['status'],'blocked')
        self.assertFalse(value['validation']['data']['scenario_ready'])
    def test_unselected_car_never_calls_photo_backend(self):
        from test_agents import analyze, envelope, pool
        with patch.dict('os.environ',DEAL_FINDER_PHOTO_IDENTITY_ENABLED='1'):
            with patch('deal_finder.agents.workflow.identify_photos',side_effect=AssertionError('Unselected')):
                value=analyze(envelope(listing=row(99,price_eur=20000)),pool(),NOW)
        self.assertEqual(value['photo_identity']['status'],'blocked')

    def test_identity_endpoints_auth_and_incomplete_listing(self):
        from fastapi.testclient import TestClient
        from deal_finder.api import app
        with patch.dict('os.environ',DEAL_FINDER_API_TOKEN='photo-test-token',DEAL_FINDER_PHOTO_IDENTITY_ENABLED=''):
            client=TestClient(app)
            self.assertEqual(client.post('/vehicles/identity-plan',json=raw()).status_code,401)
            headers={'Authorization':'Bearer photo-test-token'}
            value=raw();del value['listing']['generation']
            self.assertEqual(client.post('/vehicles/identity-plan',json=value,headers=headers).status_code,200)
            result=client.post('/vehicles/identify',json=value,headers=headers)
            self.assertEqual(result.json()['status'],'configuration_required')
    def test_provider_request_uses_images_search_and_strict_schema(self):
        import json
        from unittest.mock import Mock
        claim=response([finding('make','fiat'),finding('model','panda')])['findings']
        payload=dict(status='completed',id='synthetic-response',usage={'output_tokens':20},output=[
            dict(type='web_search_call',status='completed',action=dict(sources=[dict(url='https://fiat.com/panda')])),
            dict(type='message',content=[dict(type='output_text',text=json.dumps(claim),annotations=[])])])
        manager=Mock();manager.__enter__=Mock(return_value=Mock(read=lambda *_:json.dumps(payload).encode()));manager.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=manager
        with patch.dict('os.environ',OPENAI_API_KEY='synthetic-key',DEAL_FINDER_VISION_MODEL='synthetic-model',DEAL_FINDER_PHOTO_IDENTITY_ENABLED='1'):
            with patch('deal_finder.agents.photo_identity.build_opener',return_value=opener):
                result=research(plan(raw()))
        body=json.loads(opener.open.call_args.args[0].data)
        self.assertFalse(body['store'])
        self.assertEqual(body['tools'][0]['type'],'web_search')
        self.assertEqual(body['input'][0]['content'][1]['type'],'input_image')
        self.assertTrue(body['text']['format']['strict'])
        self.assertTrue(result['web_search_performed'])
    def test_private_photo_and_non_https_urls_rejected(self):
        for url in ('https://127.0.0.1/a','https://localhost/a','http://example.com/a','https://user:pass@example.com/a'):
            with self.assertRaises(ValueError): public_url(url)

    def test_invalid_credential_is_not_sent_or_disclosed(self):
        secret='synthetic-key\nprivate-value'
        with patch.dict('os.environ',OPENAI_API_KEY=secret,DEAL_FINDER_VISION_MODEL='chosen',DEAL_FINDER_PHOTO_IDENTITY_ENABLED='1'):
            with patch('deal_finder.agents.photo_identity.build_opener',side_effect=AssertionError('No request')):
                value=research(plan(raw()))
        self.assertEqual(value['status'],'configuration_required')
        self.assertNotIn(secret,str(value))


class VisibleDamageTests(unittest.TestCase):
    def assess(self,observations):
        output=response([])
        output['findings']['damage_observations']=observations
        return execute(raw(),NOW,lambda _:output)
    def damage(self,**changes):
        result=dict(area='front bumper',visible_signals=['visible crack'],severity='non_severe',confidence='medium',photo_indexes=[0])
        result.update(changes);return result
    def test_visible_damage_is_source_bound_and_never_repair_cost_or_inspection(self):
        value=self.assess([self.damage()])['photo_damage_assessment']
        self.assertEqual(value['observations'][0]['photo_indexes'],[0])
        self.assertTrue(value['inspection_required']);self.assertFalse(value['verified'])
        self.assertFalse(value['hidden_damage_ruled_out']);self.assertIsNone(value['repair_cost_eur'])
    def test_possible_airbag_damage_requires_review(self):
        value=self.assess([self.damage(area='steering wheel',visible_signals=['visible deployed restraint'],severity='possible_severe')])
        self.assertEqual(value['status'],'needs_review')
        self.assertTrue(value['photo_damage_assessment']['possible_severe_damage'])
    def test_invalid_photo_evidence_cannot_be_used(self):
        value=self.assess([self.damage(photo_indexes=[9])])
        self.assertEqual(value['status'],'needs_review');self.assertNotIn('photo_damage_assessment',value)
    def test_no_visible_signals_never_prove_healthy_condition(self):
        value=self.assess([])['photo_damage_assessment']
        self.assertFalse(value['hidden_damage_ruled_out']);self.assertTrue(value['inspection_required'])
