"""Measure identity extraction against independently supplied reviewed labels.

This validates label contracts and split isolation, not the authenticity of a
submitted document. No evaluation automatically promotes fields to verified.
"""
from datetime import datetime,timezone
from .vehicle_identity import resolve,normalize_field,FIELDS,VERSION
from .agents.contracts import evidence,instant


def evaluate(records,training_vehicle_ids,*,as_of=None):
    as_of=as_of or datetime.now(timezone.utc)
    if not isinstance(records,list) or len(records)>5000:raise ValueError('At most 5000 evaluation records required')
    if not isinstance(training_vehicle_ids,list) or any(not isinstance(i,str) or not i.strip() for i in training_vehicle_ids):
        raise ValueError('Explicit nonempty training identities required')
    training=set(training_vehicle_ids);seen=set();accepted=[];rejected=[]
    totals={f:dict(checked=0,correct=0,incorrect=0,abstained=0) for f in FIELDS}
    for index,row in enumerate(records):
        try:
            identity=row['vehicle_id'];labels=row['reviewed_labels'];p=row['listing']
            if not isinstance(identity,str) or not identity.strip() or identity in training or identity in seen:
                raise ValueError('Holdout vehicle is missing, duplicated or used during development')
            if labels.get('verified') is not True or not labels.get('verified_by'):
                raise ValueError('Independent reviewed labels required')
            if labels.get('origin') not in ('vehicle_document','vin_report','manufacturer_document'):
                raise ValueError('Documented labels required; seller or photo hypotheses cannot be ground truth')
            if any(labels.get(k)!=p.get(k) for k in ('source','source_id','observed_at')):
                raise ValueError('Labels must bind to the exact source observation')
            evidence(labels['evidence_url'])
            if instant(labels['checked_at'])>as_of:raise ValueError('Future labels forbidden')
            expected=labels['fields']
            if not isinstance(expected,dict) or not expected or set(expected)-set(FIELDS):raise ValueError('Known labelled identity fields required')
            normalized={f:normalize_field(f,v) for f,v in expected.items()}
            if any(v is None for v in normalized.values()):raise ValueError('Positive/explicit label values required')
            dossier=resolve(p,source_url=p.get('url'))
            for field,value in normalized.items():
                score=totals[field];score['checked']+=1
                actual=dossier['fields'][field]
                if actual['status'] in ('missing','conflict'):score['abstained']+=1
                elif actual['value']==value:score['correct']+=1
                else:score['incorrect']+=1
            seen.add(identity);accepted.append(identity)
        except (ValueError,KeyError,TypeError,AttributeError) as error:
            rejected.append(dict(index=index,reason=str(error)))
    for score in totals.values():
        answered=score['correct']+score['incorrect']
        score['accuracy_when_answered']=score['correct']/answered if answered else None
        score['coverage']=answered/score['checked'] if score['checked'] else None
    return dict(version=VERSION,status='measured' if accepted else 'needs_reviewed_holdout_labels',
        holdout_vehicles=len(accepted),training_overlap_rejected=True,fields=totals,rejected=rejected,
        supplied_labels_not_independently_authenticated=True,physical_identity_release_approved=False)
