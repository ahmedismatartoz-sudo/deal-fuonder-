import copy
import json
import unittest
from unittest.mock import patch, Mock
from datetime import datetime
from test_repair_research import request, offer, NOW
from deal_finder.parts_web import execute, plan, research
from deal_finder.repair_research import estimate_parts


def researched():
    r = request()
    o = offer()
    o['compatibility'] = [dict(requirement='diameter_mm', matches=True, evidence='Manufacturer page: 288 mm')]
    o.update(shipping_cents=None, bulky_fee_cents=None, core_deposit_cents=None)
    del o['attributes']
    part = dict(id=r['parts'][0]['id'], offers=[o], fallback=r['parts'][0]['fallback'],
                hours=dict(r['parts'][0]['hours'], source_urls=['https://www.norauto.it/p/discs.html']), missing_evidence=[])
    return dict(status='researched', findings=dict(parts=[part], unavailable_sources=[]),
                web_search_performed=True, visited_urls=[o['url'],*part['fallback']['source_urls'],'https://www.norauto.it/p/discs.html'])


class PartsWebTests(unittest.TestCase):
    def test_cited_prices_keep_provisional_wide_range_and_separate_hours(self):
        value = execute(request(), as_of=NOW, adapter=lambda _:researched())
        self.assertEqual(value['status'], 'provisional')
        estimate = value['estimate']
        self.assertEqual(estimate['items'][0]['lowest_observed_parts_offer']['normalized_cost_cents'], 9898)
        self.assertEqual(estimate['items'][0]['uncertainty'], 'wide')
        self.assertIsNone(estimate['labor_cost_cents'])
        self.assertFalse(estimate['verified_quote'])

    def test_unvisited_price_is_excluded_and_sourced_analogy_survives(self):
        result = researched(); result['findings']['parts'][0]['offers'][0]['url'] = 'https://invented.example/part'
        value = execute(request(), as_of=NOW, adapter=lambda _:result)
        item = value['estimate']['items'][0]
        self.assertEqual(item['independent_sellers'], 0)
        self.assertEqual(len(item['research_rejected_offers']), 1)
        self.assertEqual(item['basis'], 'sourced_analogy')

    def test_incomplete_fitment_is_excluded_before_cheapest_selection(self):
        for checks in ([], [dict(requirement='diameter_mm', matches=False, evidence='312 mm')],
                       [dict(requirement='diameter_mm', matches=True, evidence=''),
                        dict(requirement='diameter_mm', matches=True, evidence='duplicate')]):
            result = researched(); result['findings']['parts'][0]['offers'][0]['compatibility'] = checks
            value = execute(request(), as_of=NOW, adapter=lambda _:result)
            self.assertIsNone(value['estimate']['items'][0]['lowest_observed_parts_offer'])

    def test_no_sources_no_fabricated_estimate(self):
        result = researched(); result['findings']['parts'][0]['fallback']['source_urls'] = ['https://invented.example/analogy']
        value = execute(request(), as_of=NOW, adapter=lambda _:result)
        self.assertEqual(value['status'], 'needs_evidence')
        self.assertNotIn('estimate', value)

    def test_missing_hours_do_not_block_sourced_parts_prices_or_become_zero(self):
        result = researched(); result['findings']['parts'][0]['hours']['low_minutes'] = None
        value = execute(request(), as_of=NOW, adapter=lambda _:result)
        self.assertEqual(value['status'], 'provisional')
        self.assertIsNone(value['estimate']['items'][0]['hours']['low_minutes'])

    def test_ids_search_evidence_and_request_bounds(self):
        for change in ('id','search','duplicate'):
            result = researched()
            if change == 'id': result['findings']['parts'][0]['id']='different'
            if change == 'search': result['web_search_performed']=False
            if change == 'duplicate': result['findings']['parts'] *= 2
            self.assertEqual(execute(request(), as_of=NOW, adapter=lambda _:result)['status'], 'needs_review')
        r = request(); r['parts'] *= 9
        with self.assertRaises(ValueError): plan(r)

    def test_disabled_configuration_makes_no_paid_call(self):
        with patch.dict('os.environ', DEAL_FINDER_PARTS_WEB_ENABLED='', OPENAI_API_KEY='synthetic-key', DEAL_FINDER_PARTS_MODEL='chosen'):
            with patch('deal_finder.parts_web.build_opener', side_effect=AssertionError('No request')):
                self.assertEqual(execute(request(), as_of=NOW)['status'], 'configuration_required')

    def test_invalid_provider_key_is_never_returned_or_sent(self):
        secret='synthetic-key\nprivate-value'
        with patch.dict('os.environ',DEAL_FINDER_PARTS_WEB_ENABLED='1',OPENAI_API_KEY=secret,DEAL_FINDER_PARTS_MODEL='chosen'):
            with patch('deal_finder.parts_web.build_opener',side_effect=AssertionError('No request')):
                value=execute(request(),as_of=NOW)
        self.assertEqual(value['status'],'configuration_required')
        self.assertNotIn(secret,str(value))

    def test_provider_request_is_bounded_and_does_not_repeat_post_on_error(self):
        from urllib.error import HTTPError
        opener = Mock(); opener.open.side_effect=HTTPError('https://api.openai.com/v1/responses',429,'private body',{},None)
        with patch.dict('os.environ', DEAL_FINDER_PARTS_WEB_ENABLED='1', OPENAI_API_KEY='synthetic-key', DEAL_FINDER_PARTS_MODEL='chosen'):
            with patch('deal_finder.parts_web.build_opener',return_value=opener):
                out=execute(request(),as_of=NOW)
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(out['reason'], 'Parts provider HTTP 429')
        body=json.loads(opener.open.call_args.args[0].data)
        self.assertFalse(body['store'])
        self.assertTrue(body['text']['format']['strict'])
        self.assertEqual(body['max_tool_calls'],12)
        self.assertEqual(body['tools'][0]['type'],'web_search')

    def test_parts_price_and_delivered_price_are_distinct(self):
        cheap=offer(price_cents=4000,shipping_cents=3000,bulky_fee_cents=0,core_deposit_cents=0)
        delivered=offer(url='https://www.norauto.it/p/discs.html',seller='Norauto',seller_group='norauto',
                        price_cents=4500,shipping_cents=0,bulky_fee_cents=0,core_deposit_cents=0)
        value=estimate_parts(request([cheap,delivered]),as_of=NOW)['items'][0]
        self.assertEqual(value['lowest_observed_parts_offer']['seller'],'AUTODOC')
        self.assertEqual(value['lowest_complete_upfront_delivered_offer']['seller'],'Norauto')
        self.assertFalse(value['lowest_total_cost_verified'])

    def test_unknown_delivery_does_not_mean_free_delivery(self):
        value=estimate_parts(request(),as_of=NOW)['items'][0]
        self.assertIsNone(value['lowest_complete_upfront_delivered_offer'])

    def test_duplicate_product_retains_lower_same_time_price(self):
        value=estimate_parts(request([offer(price_cents=6000),offer(price_cents=4949)]),as_of=NOW)['items'][0]
        self.assertEqual(value['independent_sellers'],1)
        self.assertEqual(len(value['accepted_offers']),1)
        self.assertEqual(value['lowest_observed_parts_offer']['normalized_cost_cents'],9898)

    def test_wrong_part_code_and_casefold_storefront_aliases(self):
        r=request(); r['parts'][0]['part_number']='wrong-sku'
        self.assertEqual(estimate_parts(r,as_of=NOW)['items'][0]['independent_sellers'],0)
        r=request([offer(),offer(url='https://autoparti.it/part',seller='autodoc se',seller_group='second')])
        self.assertEqual(estimate_parts(r,as_of=NOW)['items'][0]['independent_sellers'],1)

    def test_authenticated_research_endpoint(self):
        from fastapi.testclient import TestClient
        from deal_finder.api import app
        with patch.dict('os.environ',DEAL_FINDER_API_TOKEN='test-token',DEAL_FINDER_PARTS_WEB_ENABLED=''):
            with TestClient(app) as client:
                self.assertEqual(client.post('/repairs/research',json=request()).status_code,401)
                value=client.post('/repairs/research',json=request(),headers={'Authorization':'Bearer test-token'})
                self.assertEqual(value.json()['status'],'configuration_required')
                self.assertEqual(client.post('/repairs/research',json={},headers={'Authorization':'Bearer test-token'}).status_code,422)
