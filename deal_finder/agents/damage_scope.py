"""Keep observed damage, diagnosed repairs and parts hypotheses distinct."""


def execute(raw, selected, condition):
    result = dict(status='blocked' if not selected else 'needs_evidence',
                  repairs_known_complete=False, zero_repairs_confirmed=False,
                  required_repairs=[], parts_hypotheses=[], hidden_damage_excluded=False,
                  reasons=[], observed_damage=[], necessary_work=[], cosmetic_work=[],
                  seller_questions=['Confirm total price, exact variant, mileage and repair history'],
                  photo_diagnosis_certified=False)
    if not selected:
        result['reasons'] = ['Market selection precedes damage and parts checks.']
        return result
    if condition['status'] == 'completed':
        result.update(status='documented_inspection', repairs_known_complete=True,
                      required_repairs=condition['data']['required_repairs'],
                      zero_repairs_confirmed=not condition['data']['required_repairs'])
    request = raw.get('parts_research')
    if isinstance(request, dict) and isinstance(request.get('parts'), list):
        result['parts_hypotheses'] = [dict(id=p.get('id'), name=p.get('name'), quantity=p.get('quantity'),
                                           diagnosis_confirmed=p.get('diagnosis_confirmed') is True)
                                      for p in request['parts'] if isinstance(p, dict)]
        if result['status'] == 'needs_evidence':
            result['status'] = 'provisional_scope'
    if not result['repairs_known_complete']:
        result['reasons'].append('Seller claims and photographs cannot establish complete damage scope; inspect hidden damage.')
    if not result['parts_hypotheses'] and result['required_repairs']:
        result['reasons'].append('Convert diagnosed operations to compatible parts and consumables before pricing.')
    from .professional import dossier
    from ..models import Listing
    try:
        target=Listing.parse(raw['listing'])
        # Organize supplied observations; these do not grant an inspection attestation.
        observations=dossier(raw).get('damage_observations',[])
        if not isinstance(observations,list) or len(observations)>100:
            raise ValueError('Damage observations must be an array of at most 100 entries')
        images=target.image_urls or []
        for observation in observations:
            if not isinstance(observation,dict):
                result['reasons'].append('Damage observation must be an object');continue
            indexes=observation.get('photo_indexes',[])
            if (not isinstance(indexes,list) or any(type(i) is not int or not 0<=i<len(images) for i in indexes)
                    or observation.get('origin') not in ('photo','listing','inspection')
                    or not observation.get('finding')):
                result['reasons'].append('Damage observation needs a valid origin, finding and photo references');continue
            if observation['origin']=='photo' and not indexes:
                result['reasons'].append('Visible damage requires an actual source photo reference');continue
            result['observed_damage'].append(dict(finding=observation['finding'],origin=observation['origin'],
                photo_indexes=indexes,repair_id=observation.get('repair_id'),diagnosis_verified=False))
        work=dossier(raw).get('work_classification',[])
        if not isinstance(work,list):raise ValueError('Work classification must be an array')
        for operation in work:
            if (not isinstance(operation,dict) or operation.get('repair_id') not in result['required_repairs']
                    or operation.get('category') not in ('necessary','cosmetic') or not operation.get('reason')):
                result['reasons'].append('Work classification must refer to an inspected repair and explain its category');continue
            key='necessary_work' if operation['category']=='necessary' else 'cosmetic_work'
            result[key].append(dict(operation))
        if target.condition=='damaged' or target.damage_indicators or target.damage_severity=='severe':
            result['seller_questions'].extend(['Provide pre-repair photos and incident history',
                'Allow structural/restraint inspection and diagnostic scan',
                'Confirm dismantling findings and external bodyshop scope'])
    except (ValueError,KeyError,TypeError,AttributeError):
        result['reasons'].append('Damage evidence organization requires valid source data')
    return result
