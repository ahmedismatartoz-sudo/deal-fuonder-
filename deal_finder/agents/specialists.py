from datetime import timedelta
from ..models import Listing, normalize
from ..pricing import estimate
from .contracts import Result, instant, evidence, bounded_cost, verified_identity, PIPELINE_VERSION

class QualityAgent:
    name = 'quality'
    requires = ()
    purpose = 'Validate and normalize listings; never guess missing specifications.'

    def execute(self, ctx):
        try:
            if ctx.raw.get('_intake_error'):
                raise ValueError(ctx.raw['_intake_error'])
            ctx.target = Listing.parse(ctx.raw['listing'])
            if instant(ctx.target.observed_at) > ctx.as_of:
                raise ValueError('Listing was not available at analysis time')
            return Result(self.name, 'completed', {'listing': ctx.target.to_dict()})
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
            return Result(self.name, 'quarantined', reasons=[str(error)])

class IdentityAgent:
    name = 'identity'
    requires = ('quality',)
    purpose = 'Detect conflicting verified vehicle identities and expose unknown duplicates.'

    def execute(self, ctx):
        target = ctx.target
        if not verified_identity(ctx.raw.get('identity_evidence') or ctx.identity_evidence.get((*target.identity, target.observed_at)), target, ctx.as_of):
            return Result(self.name, 'needs_review', reasons=['Documented identity attestation missing or invalid; cross-source identity unresolved.'])
        conflicts = [x.url for x in ctx.candidates
                     if x.vehicle_id == target.vehicle_id
                     and any(getattr(x, k) != getattr(target, k)
                             for k in ('make', 'model', 'generation', 'fuel', 'transmission', 'year'))]
        if conflicts:
            return Result(self.name, 'needs_review', {'conflicting_urls': conflicts}, ['Vehicle identity has incompatible specifications.'])
        return Result(self.name, 'completed', {'vehicle_id': target.vehicle_id,
                      'duplicate_source_ids': [list(x.identity) for x in ctx.candidates
                                               if x.vehicle_id == target.vehicle_id and x.identity != target.identity]})

class MarketAgent:
    name = 'market'
    requires = ('quality',)
    purpose = 'Compute explainable asking-price benchmarks using current comparable evidence.'

    def execute(self, ctx):
        if any(x.identity == ctx.target.identity and instant(x.observed_at) > instant(ctx.target.observed_at) for x in ctx.candidates):
            return Result(self.name, 'blocked', reasons=['Target snapshot superseded by a newer observation.'])
        if not ctx.target.active:
            return Result(self.name, 'blocked', reasons=['Target listing is inactive.'])
        if ctx.as_of - instant(ctx.target.observed_at) > timedelta(days=30):
            return Result(self.name, 'blocked', reasons=['Target listing is stale.'])
        if ctx.source_asking_candidates is not None:
            from ..market import asking_benchmark
            result = asking_benchmark(ctx.target, ctx.source_asking_candidates, ctx.as_of)
            result['vehicle_identity_verified'] = False
            status = 'completed' if result['status'] == 'benchmark_available' else 'blocked'
            return Result(self.name, status, result, result['warnings'])
        vetted = [x for x in ctx.candidates if verified_identity(ctx.identity_evidence.get((*x.identity, x.observed_at)), x, ctx.as_of)]
        result = estimate(ctx.target, vetted, as_of=ctx.as_of)
        if result['status'] != 'benchmark_available':
            national = estimate(ctx.target, vetted, as_of=ctx.as_of, scope='national')
            if national['status'] == 'benchmark_available':
                result = national
        result['excluded_unverified_identity_count'] = len(ctx.candidates) - len(vetted)
        status = 'completed' if result['status'] == 'benchmark_available' else 'blocked'
        return Result(self.name, status, result, result['warnings'])

class ConditionAgent:
    name = 'condition'
    requires = ('quality', 'identity')
    purpose = 'Read documented inspections; photographs and seller claims cannot prove no damage.'

    def execute(self, ctx):
        inspection = ctx.raw.get('inspection')
        if not isinstance(inspection, dict):
            return Result(self.name, 'needs_review', reasons=['Documented inspection missing.'])
        try:
            if inspection.get('verified') is not True or inspection.get('complete') is not True:
                raise ValueError('Inspection needs complete scope and explicit verification')
            if not isinstance(inspection.get('verified_by'), str) or not inspection['verified_by'].strip():
                raise ValueError('Inspection requires verifier identity')
            if inspection['vehicle_id'] != ctx.target.vehicle_id:
                raise ValueError('Inspection belongs to another vehicle')
            date = instant(inspection['inspected_at'])
            if not timedelta(0) <= ctx.as_of - date <= timedelta(days=30):
                raise ValueError('Inspection is stale or from the future')
            url = evidence(inspection.get('evidence_url'))
            repairs = inspection.get('required_repairs')
            if not isinstance(repairs, list) or any(not isinstance(x, str) or not x.strip() for x in repairs):
                raise ValueError('Inspection must explicitly list required repairs, including an empty list if none')
            repairs = [normalize(x) for x in repairs]
            if len(set(repairs)) != len(repairs):
                raise ValueError('Duplicate repair identifiers in inspection')
            if ctx.target.condition == 'damaged' and not repairs:
                raise ValueError('Damaged listing conflicts with inspection claiming no repairs')
            return Result(self.name, 'completed', {'required_repairs': repairs,
                          'evidence_url': url, 'inspected_at': date.isoformat(),
                          'verification': 'human_attestation', 'verified_by': inspection['verified_by']})
        except (ValueError, KeyError, TypeError) as error:
            return Result(self.name, 'needs_review', reasons=[str(error)])

class RepairAgent:
    name = 'repair'
    requires = ('quality', 'condition')
    purpose = 'Total documented repair quotes; absence of evidence never means zero cost.'

    def execute(self, ctx):
        repairs = ctx.results['condition'].data['required_repairs']
        if not repairs:
            return Result(self.name, 'completed', {'low_cents': 0, 'high_cents': 0, 'quotes': [],
                          'basis': 'documented_inspection_no_repairs'})
        try:
            quotes = ctx.raw.get('repair_quotes', [])
            if not isinstance(quotes, list):
                raise ValueError('repair_quotes must be an array')
            by_repair = {}
            for quote in quotes:
                code = normalize(quote['repair_id'])
                if code in by_repair:
                    raise ValueError('Multiple quotes for one repair; choose and attest one complete quote')
                if quote['vehicle_id'] != ctx.target.vehicle_id:
                    raise ValueError('Quote vehicle identity mismatch')
                if quote.get('scope') != 'parts_and_labor':
                    raise ValueError('Quotes must cover parts and labor')
                low, high = bounded_cost(quote, ctx.as_of)
                by_repair[code] = (low, high, quote)
            if set(by_repair) != set(repairs):
                raise ValueError('Repair quotes must cover exactly the inspected repairs')
            return Result(self.name, 'completed', {
                'low_cents': sum(x[0] for x in by_repair.values()),
                'high_cents': sum(x[1] for x in by_repair.values()),
                'quotes': [x[2] for x in by_repair.values()], 'basis': 'human_attested_quotes'})
        except (ValueError, KeyError, TypeError) as error:
            return Result(self.name, 'needs_review', reasons=[str(error)])

class OpportunityAgent:
    name = 'opportunity'
    requires = ('quality', 'identity', 'market', 'repair')
    purpose = 'Calculate explicitly labelled scenarios with all cost categories and no promised profit.'
    cost_categories = ('transfer', 'transport', 'preparation', 'warranty', 'taxes', 'fees', 'contingency')

    def execute(self, ctx):
        try:
            costs = ctx.raw.get('operating_costs')
            if not isinstance(costs, dict) or set(costs) != set(self.cost_categories):
                raise ValueError('All seven operating cost categories must be explicitly documented')
            lines = {name: bounded_cost(cost, ctx.as_of) for name, cost in costs.items()}
            repairs = ctx.results['repair'].data
            low = sum(x[0] for x in lines.values()) + repairs['low_cents']
            high = sum(x[1] for x in lines.values()) + repairs['high_cents']
            market = ctx.results['market'].data
            acquisition = ctx.target.price_eur * 100
            # These are observed quartile scenarios, not probabilistic bounds.
            pessimistic = market['observed_range_eur']['p25'] * 100 - acquisition - high
            optimistic = market['observed_range_eur']['p75'] * 100 - acquisition - low
            return Result(self.name, 'completed', {
                'basis': 'asking_price_scenario_only', 'acquisition_asking_cents': acquisition,
                'total_cost_low_cents': low, 'total_cost_high_cents': high,
                'scenario_low_cents': pessimistic, 'scenario_high_cents': optimistic,
                'cost_evidence': costs, 'forecast_profit_cents': None,
                'buy_recommendation': False,
                'requires': ['validated_transaction_price_model', 'verified_acquisition_terms']},
                ['Scenario based on asking prices; not expected profit or a purchase recommendation.'])
        except (ValueError, KeyError, TypeError) as error:
            return Result(self.name, 'blocked', reasons=[str(error)])

class ValidationAgent:
    name = 'validation'
    requires = ()
    purpose = 'Gate outputs, list missing evidence and keep forecasts disabled until calibrated.'

    def execute(self, ctx):
        unresolved = {name: result.reasons for name, result in ctx.results.items() if result.status != 'completed'}
        market = ctx.results.get('market')
        duplicate_risk = bool(market and any('duplicates may remain' in w for w in market.reasons))
        if duplicate_risk:
            unresolved['market_identity_coverage'] = ['Comparable vehicle identities require verification.']
        return Result(self.name, 'completed', {
            'analysis_state': 'needs_evidence' if unresolved else 'scenario_ready',
            'missing_or_blocked': unresolved,
            'scenario_ready': not unresolved,
            'forecast_ready': False,
            'buy_recommendation': False,
            'forecast_block_reason': 'No calibrated sale-price model or independent transaction validation yet.'})

def selection_decision(target, market):
    """Provisional screening policy, distinct from predicted profit."""
    policy = dict(min_price_eur=1000, max_price_eur=50000, min_discount_fraction=0.10,
                  basis='asking_price_p25', calibrated=False)
    if target is None or market is None or market.status != 'completed':
        return dict(candidate=False, route='enrichment', policy=policy, reason='Reliable asking benchmark unavailable')
    lower = market.data['observed_range_eur']['p25']
    discount = (lower - target.price_eur) / lower
    candidate = (target.active and target.price_kind == 'total'
                 and target.condition != 'unknown' and 1000 <= target.price_eur <= 50000 and discount >= policy['min_discount_fraction'])
    if target.condition == 'unknown':
        return dict(candidate=False, route='enrichment', policy=policy, reason='Condition unresolved')
    return dict(candidate=candidate, route='verification' if candidate else 'screened_out', policy=policy, discount_fraction=round(discount, 6),
                reason='Passed price screening; inspection and costs still required' if candidate
                       else 'Outside budget or insufficient discount from observed lower quartile')


AGENTS = (QualityAgent(), IdentityAgent(), MarketAgent(), ConditionAgent(), RepairAgent(), OpportunityAgent(), ValidationAgent())


def registry():
    from .intake import IntakeAgent
    from .evaluation import EvaluationAgent
    return [dict(name='intake', version=PIPELINE_VERSION, requires=[], mode='ingestion', purpose=IntakeAgent.purpose)] + [
        dict(name=x.name, version=PIPELINE_VERSION, requires=list(x.requires), mode='analysis', purpose=x.purpose)
        for x in AGENTS] + [dict(name='evaluation', version=PIPELINE_VERSION, requires=[], mode='evaluation', purpose=EvaluationAgent.purpose)]


def analyze(raw, candidates, as_of, agents=AGENTS, identity_evidence=None, source_asking_candidates=None):
    from .contracts import Context
    ctx = Context(raw=raw, candidates=candidates, as_of=as_of, identity_evidence=identity_evidence or {},
                  source_asking_candidates=source_asking_candidates)
    for agent in agents:
        if agent.name in ('condition', 'repair', 'opportunity') and not selection_decision(ctx.target, ctx.results.get('market'))['candidate']:
            result = Result(agent.name, 'blocked', reasons=['Price screening did not select this listing.'])
        elif not ctx.ready(*agent.requires):
            result = Result(agent.name, 'blocked', reasons=[f'Dependency unavailable: {name}'
                            for name in agent.requires if not ctx.ready(name)])
        else:
            result = agent.execute(ctx)
        ctx.results[agent.name] = result
    return {name: value.to_dict() for name, value in ctx.results.items()}
