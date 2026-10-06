"""Keep observed damage, diagnosed repairs and parts hypotheses distinct."""


def execute(raw, selected, condition):
    result = dict(status='blocked' if not selected else 'needs_evidence',
                  repairs_known_complete=False, zero_repairs_confirmed=False,
                  required_repairs=[], parts_hypotheses=[], hidden_damage_excluded=False,
                  reasons=[])
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
    return result
