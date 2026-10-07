"""Learn asking-price adjustments from retained comparable vehicles.

This agent estimates advertised prices, never completed-sale proceeds. It
excludes the target from learning and validates on separate vehicle identities.
"""
import hashlib
from statistics import median

VERSION = 'market-price-skills-v1'


def skills():
    return [dict(name='match_variant', purpose='Keep engine, transmission, trim and seller cohorts separate'),
            dict(name='learn_year_and_mileage', purpose='Fit adjustments from independent archived vehicles'),
            dict(name='validate_price_model', purpose='Measure error on vehicles excluded from fitting'),
            dict(name='detect_price_anomaly', purpose='Compare target asking amount with adjusted source prices'),
            dict(name='explain_uncertainty', purpose='Return sample size, source evidence and unresolved factors')]


def percentile(values, fraction):
    values=sorted(values)
    return values[int((len(values)-1)*fraction)]


def unique(rows):
    result={}
    for p in rows:
        if any(type(p.get(k)) is not int for k in ('year','mileage_km','price_eur')) or p['price_eur']<=0:
            continue
        key=(p['year'],p['mileage_km'],p['price_eur'],p.get('city'),p.get('version_text'))
        result.setdefault(key,p)
    return list(result.values())


def fit(rows, target):
    # Independent variation is essential: age and mileage cannot be disentangled
    # from a small sample or vehicles all driven at the same rate per year.
    if len(rows)<12 or len({p['year'] for p in rows})<3 or len({p['mileage_km'] for p in rows})<5:
        return None
    if max(p['mileage_km'] for p in rows)-min(p['mileage_km'] for p in rows)<50000:
        return None
    xs=[[1.,p['year']-target['year'],(p['mileage_km']-target['mileage_km'])/10000.] for p in rows]
    ys=[float(p['price_eur']) for p in rows]
    weights=[1.]*len(rows)
    beta=None
    for iteration in range(4):
        matrix=[[sum(w*x[a]*x[b] for w,x in zip(weights,xs)) for b in range(3)]
                +[sum(w*x[a]*y for w,x,y in zip(weights,xs,ys))] for a in range(3)]
        for column in range(3):
            pivot=max(range(column,3),key=lambda i:abs(matrix[i][column]))
            if abs(matrix[pivot][column])<1e-8:
                return None
            matrix[column],matrix[pivot]=matrix[pivot],matrix[column]
            scale=matrix[column][column]
            matrix[column]=[v/scale for v in matrix[column]]
            for row in range(3):
                if row==column:continue
                scale=matrix[row][column]
                matrix[row]=[a-scale*b for a,b in zip(matrix[row],matrix[column])]
        beta=[matrix[i][3] for i in range(3)]
        residuals=[y-sum(b*v for b,v in zip(beta,x)) for x,y in zip(xs,ys)]
        centre=median(residuals)
        deviation=median(abs(r-centre) for r in residuals)*1.4826
        if deviation<1e-8:break
        weights=[min(1.,1.5*deviation/max(abs(r-centre),1e-8)) for r in residuals]
    # A contradictory slope is an unsupported model, not a reason to impose
    # hand-written depreciation percentages on the archive.
    if beta[0]<=0 or beta[1]<-1e-6 or beta[2]>1e-6:
        return None
    return beta


def assess(target, cohort, nearby):
    identity=(target.get('source'),target.get('source_id'))
    cohort=unique([p for p in cohort if (p.get('source'),p.get('source_id'))!=identity])
    nearby=unique([p for p in nearby if (p.get('source'),p.get('source_id'))!=identity])
    result=dict(agent='market_prices',version=VERSION,skills=[s['name'] for s in skills()],
                basis='published_asking_prices',cohort_count=len(cohort),comparable_count=len(nearby),
                target_excluded_from_training=True,method='unadjusted_comparables',
                confidence='low',adjustments=None,validation=None,
                unresolved_factors=['verified_identity','damage','maintenance','final_purchase_price',
                                    'completed_sale_price','selling_costs'],buy_recommendation=False)
    if not nearby:
        return dict(result,status='needs_comparables')
    beta=None
    if (type(target.get('year')) is int and type(target.get('mileage_km')) is int and cohort
            and min(p['year'] for p in cohort)<=target['year']<=max(p['year'] for p in cohort)
            and min(p['mileage_km'] for p in cohort)<=target['mileage_km']<=max(p['mileage_km'] for p in cohort)):
        ordered=sorted(cohort,key=lambda p:hashlib.sha256(str((p.get('source'),p.get('source_id'))).encode()).hexdigest())
        validation=ordered[::5] if len(ordered)>=20 else []
        held_ids={(p.get('source'),p.get('source_id')) for p in validation}
        training=[p for p in ordered if (p.get('source'),p.get('source_id')) not in held_ids]
        trial=fit(training,target)
        if trial is not None and validation:
            errors=[abs(p['price_eur']-(trial[0]+trial[1]*(p['year']-target['year'])
                         +trial[2]*(p['mileage_km']-target['mileage_km'])/10000)) for p in validation]
            error=median(errors)
            ratio=error/median(p['price_eur'] for p in validation)
            result['validation']=dict(training_vehicles=len(training),held_out_vehicles=len(validation),
                median_absolute_error_eur=round(error),relative_error=round(ratio,4),
                holdout_excluded_from_fit=True)
            if ratio<=0.20:
                beta=trial
                result['confidence']='medium' if ratio>0.10 or len(cohort)<40 else 'high'
                if len(nearby)<8:
                    result['confidence']='medium'
        elif trial is not None and not validation:
            beta=trial
    adjusted=[]
    for p in nearby:
        value=p['price_eur']
        if beta is not None:
            value-=beta[1]*(p['year']-target['year'])+beta[2]*(p['mileage_km']-target['mileage_km'])/10000
        adjusted.append(dict(source=p.get('source'),source_id=p.get('source_id'),url=p.get('url'),
            year=p['year'],mileage_km=p['mileage_km'],asking_eur=p['price_eur'],adjusted_asking_eur=max(1,round(value))))
    prices=[p['adjusted_asking_eur'] for p in adjusted]
    if beta is not None:
        result.update(method='archive_trained_year_mileage',adjustments=dict(
            euro_per_newer_year=round(beta[1],2),euro_per_extra_10000_km=round(beta[2],2)))
    return dict(result,status='provisional_price_context',source_adjustments=adjusted,
                reference_basis='lowest_observed_or_adjusted_comparable',
                asking_low_eur=min(min(prices),min(p['price_eur'] for p in nearby)),
                asking_typical_eur=round(median(prices)),
                asking_high_eur=percentile(prices,.75))
