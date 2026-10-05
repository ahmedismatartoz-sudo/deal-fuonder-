import unittest
from datetime import timedelta
from test_core import row, NOW
from deal_finder.models import Listing
from deal_finder.agents import analyze as run_agents, registry
from deal_finder.agents.contracts import Result
from deal_finder.agents.evaluation import EvaluationAgent


def proof(vehicle_id):
    return dict(vehicle_id=vehicle_id,verified=True,verified_by='inspector-test',
                evidence_url='https://example.com/identity', verified_at=(NOW-timedelta(days=1)).isoformat())

def analyze(raw, candidates, as_of):
    proofs = {(*x.identity,x.observed_at): proof(x.vehicle_id) for x in candidates if x.vehicle_id}
    return run_agents(raw,candidates,as_of,identity_evidence=proofs)

def cost(low=10000, high=20000, **overrides):
    data = dict(low_cents=low, high_cents=high, verified=True, verified_by='inspector-test',
                evidence_url='https://example.com/quote', currency='EUR', tax_included=True,
                quoted_at=(NOW-timedelta(days=1)).isoformat(), valid_until=(NOW+timedelta(days=30)).isoformat())
    data.update(overrides)
    return data


def envelope(**changes):
    data = dict(listing=row(99, price_eur=8000), identity_evidence=proof('vehicle-99'), inspection=dict(vehicle_id='vehicle-99',
                inspected_at=(NOW-timedelta(days=1)).isoformat(), verified=True, complete=True,
                verified_by='inspector-test', evidence_url='https://example.com/inspection', required_repairs=[]),
                operating_costs={name:cost() for name in ('transfer','transport','preparation','warranty','taxes','fees','contingency')})
    data.update(changes)
    return data


def pool():
    return [Listing.parse(row(i,price_eur=10000+i*100)) for i in range(8)]

class AgentTests(unittest.TestCase):
    def test_registry_dependencies_ordered(self):
        visited=set()
        for agent in registry():
            self.assertTrue(set(agent['requires']).issubset(visited))
            visited.add(agent['name'])
        self.assertEqual(len(visited),7)

    def test_full_scenario_is_never_forecast(self):
        out=analyze(envelope(),pool(),NOW)
        scenario=out['opportunity']['data']
        self.assertEqual(scenario['scenario_low_cents'],10100*100-8000*100-7*20000)
        self.assertEqual(scenario['forecast_profit_cents'],None)
        self.assertFalse(scenario['buy_recommendation'])
        self.assertTrue(out['validation']['data']['scenario_ready'])
        self.assertFalse(out['validation']['data']['forecast_ready'])

    def test_invalid_listing_blocks_dependencies(self):
        out=analyze(envelope(listing=row(99,price_eur=-1)),pool(),NOW)
        self.assertEqual(out['quality']['status'],'quarantined')
        self.assertEqual(out['market']['status'],'blocked')
        self.assertFalse(out['validation']['data']['scenario_ready'])

    def test_absent_inspection_or_identity_is_never_zero(self):
        out=analyze(envelope(inspection=None),pool(),NOW)
        self.assertEqual(out['condition']['status'],'needs_review')
        self.assertEqual(out['repair']['data'],{})
        out=analyze(envelope(listing=row(99,vehicle_id=None)),pool(),NOW)
        self.assertEqual(out['identity']['status'],'needs_review')

    def test_conflicting_vehicle_specifications(self):
        out=analyze(envelope(),pool()+[Listing.parse(row(40,vehicle_id='vehicle-99',model='500'))],NOW)
        self.assertEqual(out['identity']['status'],'needs_review')

    def test_quotes_must_cover_parts_labor_and_all_repairs(self):
        raw=envelope(); raw['inspection']['required_repairs']=['brakes']
        raw['repair_quotes']=[dict(cost(30000,50000), repair_id='brakes',vehicle_id='vehicle-99',scope='parts_and_labor')]
        out=analyze(raw,pool(),NOW)
        self.assertEqual(out['repair']['data']['high_cents'],50000)
        raw['repair_quotes'][0]['scope']='parts_only'
        self.assertEqual(analyze(raw,pool(),NOW)['repair']['status'],'needs_review')
        raw['repair_quotes']=[]
        self.assertEqual(analyze(raw,pool(),NOW)['repair']['status'],'needs_review')

    def test_cost_evidence_rejected_when_incomplete_expired_or_negative(self):
        for change in ({'verified':False}, {'low_cents':-1}, {'high_cents':1}, {'tax_included':False},
                       {'valid_until':(NOW-timedelta(days=1)).isoformat()}, {'currency':'USD'}):
            raw=envelope();raw['operating_costs']['transport']=cost(**change)
            self.assertEqual(analyze(raw,pool(),NOW)['opportunity']['status'],'blocked')
        raw=envelope();del raw['operating_costs']['taxes']
        self.assertEqual(analyze(raw,pool(),NOW)['opportunity']['status'],'blocked')

    def test_unknown_comparable_identity_blocks_release(self):
        candidates=[Listing.parse(row(i,vehicle_id=None)) for i in range(8)]
        out=analyze(envelope(),candidates,NOW)
        self.assertFalse(out['validation']['data']['scenario_ready'])

    def test_stale_inactive_and_superseded_target(self):
        for target in (row(99,active=False),row(99,observed_at=(NOW-timedelta(days=40)).isoformat())):
            self.assertEqual(analyze(envelope(listing=target),pool(),NOW)['market']['status'],'blocked')
        out=analyze(envelope(),pool()+[Listing.parse(row(99,observed_at=NOW.isoformat()))],NOW)
        self.assertEqual(out['market']['status'],'blocked')

class EvaluationTests(unittest.TestCase):
    def sample(self,i=0):
        return dict(vehicle_id=f'holdout-{i}',model_version='sale-v1',verified=True,verified_by='test',
                    data_kind='observed_transaction',evidence_url='https://example.com/transaction',
                    training_cutoff=(NOW-timedelta(days=100)).isoformat(),
                    predicted_at=(NOW-timedelta(days=20)).isoformat(),sold_at=(NOW-timedelta(days=1)).isoformat(),
                    predicted_cents=1100000,sold_cents=1000000,interval_low_cents=900000,
                    interval_high_cents=1200000,segment='fiat-panda-milano')
    def evaluate(self,rows,training=()):
        return EvaluationAgent().execute(rows,model_version='sale-v1',training_vehicle_ids=list(training),as_of=NOW)
    def test_metrics(self):
        result=self.evaluate([self.sample(i) for i in range(30)])
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.data['metrics']['mae_cents'],100000)
        self.assertEqual(result.data['metrics']['median_absolute_percentage_error'],10)
        self.assertEqual(result.data['metrics']['interval_coverage'],1)
        self.assertFalse(result.data['forecast_release_approved'])
    def test_temporal_identity_and_training_leakage(self):
        rows=[self.sample(i) for i in range(30)]; rows[0]['predicted_at']=NOW.isoformat()
        self.assertEqual(self.evaluate(rows).status,'blocked')
        self.assertEqual(self.evaluate([self.sample(i) for i in range(30)],['holdout-0']).status,'blocked')
        self.assertEqual(self.evaluate([self.sample() for _ in range(30)]).data['accepted_count'],1)
    def test_small_sample_and_future_sale(self):
        self.assertEqual(self.evaluate([self.sample()]).status,'blocked')
        sample=self.sample();sample['sold_at']=(NOW+timedelta(days=1)).isoformat()
        self.assertEqual(self.evaluate([sample]).data['accepted_count'],0)
