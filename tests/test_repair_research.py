import copy
import unittest
from deal_finder.repair_research import estimate_parts, search_plan

NOW = '2026-10-06T00:00:00+00:00'


def offer(**changes):
    data = dict(url='https://www.auto-doc.it/brembo/1657442', observed_at=NOW,
                currency='EUR', vat_included=True, availability='available', condition='new',
                tier='aftermarket', fitment_basis='specified brake dimensions; VIN still unresolved',
                attributes={'diameter_mm': 288}, price_cents=4949, pack_quantity=1,
                seller='AUTODOC', seller_group='autodoc', brand='BREMBO', part_number='09.9145.14',
                retrieval='indexed_page')
    data.update(changes)
    return data


def request(offers=None):
    return dict(vehicle={'make': 'Volkswagen', 'model': 'Golf VII'}, parts=[dict(
        id='front_discs', name='Dischi anteriori', quantity=2, condition='new', tier='aftermarket',
        requirements={'diameter_mm': 288}, diagnosis_confirmed=False, identity_confirmed=False,
        fallback=dict(low_cents=7500, high_cents=16000,
                      basis='Provisional same-size pair envelope, not calibrated',
                      source_urls=['https://www.norauto.it/p/2-dischi-brembo-referenza-09.9145.14-216244.html']),
        hours=dict(low_minutes=60, high_minutes=150, basis='Synthetic planning assumption, not a workshop time guide'),
        offers=offers if offers is not None else [offer()])])


class RepairResearchTests(unittest.TestCase):
    def test_authenticated_api_estimate_and_plan(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from deal_finder.api import app
        with patch.dict('os.environ', {'DEAL_FINDER_API_TOKEN': 'test-repair-token'}):
            with TestClient(app) as client:
                self.assertEqual(client.post('/repairs/estimate', json=request()).status_code, 401)
                headers = {'Authorization': 'Bearer test-repair-token'}
                self.assertEqual(client.post('/repairs/estimate', json=request(), headers=headers).status_code, 200)
                self.assertEqual(client.post('/repairs/estimate', json={}, headers=headers).status_code, 422)
                self.assertEqual(client.post('/repairs/search-plan', json={'vehicle': {},
                    'part': {'name': 'faro'}}, headers=headers).status_code, 200)

    def test_real_disc_unit_and_pair_prices_are_normalized(self):
        r = request([offer(), offer(url='https://www.norauto.it/p/discs.html', seller_group='norauto',
                                  seller='NORAUTO', price_cents=10999, pack_quantity=2)])
        result = estimate_parts(r, as_of=NOW)
        self.assertEqual([x['normalized_cost_cents'] for x in result['items'][0]['accepted_offers']], [9898, 10999])
        self.assertIsNone(result['labor_cost_cents'])
        self.assertFalse(result['calibrated'])
        self.assertFalse(result['hours_additive'])

    def test_less_information_widens_interval(self):
        r = request([offer(retrieval='live_page'), offer(url='https://www.norauto.it/p/discs.html',
            seller_group='norauto', seller='NORAUTO', price_cents=10999, pack_quantity=2, retrieval='live_page')])
        r['parts'][0].update(diagnosis_confirmed=True, identity_confirmed=True)
        narrow = estimate_parts(r, as_of=NOW)
        r['parts'][0]['identity_confirmed'] = False
        wide = estimate_parts(r, as_of=NOW)
        self.assertLessEqual(wide['parts_low_cents'], narrow['parts_low_cents'])
        self.assertGreaterEqual(wide['parts_high_cents'], narrow['parts_high_cents'])

    def test_missing_offers_still_return_sourced_provisional_number(self):
        result = estimate_parts(request([]), as_of=NOW)
        self.assertEqual(result['parts_typical_cents'], 11750)
        self.assertEqual(result['items'][0]['basis'], 'sourced_analogy')

    def test_bad_observations_cannot_enter_price_average(self):
        for bad in ({'attributes': {'diameter_mm': 312}}, {'pack_quantity': 0},
                    {'vat_included': None}, {'availability': 'unknown'}, {'condition': 'used'},
                    {'tier': 'original'}, {'currency': 'GBP'}, {'observed_at': '2025-01-01T00:00:00+00:00'},
                    {'observed_at': '2027-01-01T00:00:00+00:00'}, {'price_cents': True},
                    {'url': 'http://seller.example/part'}, {'fitment_basis': ''}):
            with self.subTest(bad=bad):
                result = estimate_parts(request([offer(**bad)]), as_of=NOW)
                self.assertEqual(result['items'][0]['independent_sellers'], 0)
                self.assertEqual(len(result['items'][0]['rejected_offers']), 1)

    def test_aliases_and_duplicate_products_do_not_multiply_evidence(self):
        result = estimate_parts(request([offer(), offer(url='https://www.autoparti.it/brembo.html',
                                    seller_group='autoparti')]), as_of=NOW)
        self.assertEqual(result['items'][0]['independent_sellers'], 1)
        self.assertEqual(len(result['items'][0]['accepted_offers']), 1)

    def test_kit_missing_slave_cylinder_is_rejected(self):
        r = request([offer(attributes={'csc': False})])
        r['parts'][0]['requirements'] = {'csc': True}
        self.assertEqual(len(estimate_parts(r, as_of=NOW)['items'][0]['rejected_offers']), 1)

    def test_no_unattributed_fallback_or_invalid_hours(self):
        for field in ('basis', 'source_urls'):
            r = request([]); r['parts'][0]['fallback'][field] = ''
            with self.assertRaises(ValueError): estimate_parts(r, as_of=NOW)
        r = request(); r['parts'][0]['hours']['high_minutes'] = 0
        with self.assertRaises(ValueError): estimate_parts(r, as_of=NOW)

    def test_duplicate_required_line_rejected(self):
        r = request(); r['parts'].append(copy.deepcopy(r['parts'][0]))
        with self.assertRaises(ValueError): estimate_parts(r, as_of=NOW)

    def test_plan_searches_exact_code_and_discloses_missing_identity(self):
        plan = search_plan({'make': 'Fiat', 'model': 'Panda'},
                           {'name': 'faro sinistro', 'part_number': 'FT1244804'})
        self.assertEqual(len(plan['queries']), 4)
        self.assertIn('generation', plan['missing_vehicle_fields'])
        self.assertIn('FT1244804', plan['exact_code_query'])
