"""Text/source triage, never an inspection or repair-cost estimate."""
import re

VERSION='non-severe-damage-mix-v1'
MIX={'clean':.30,'minimal':.20,'non_severe':.50}
PATTERNS={
 'severe':r'\b(?:telaio\s+(?:piegato|danneggiato|deformato)|danni?\s+struttural\w*|'
           r'airbag.{0,24}(?:esplos\w*|scoppi\w*|apert\w*|attivat\w*)|'
           r'alluvionat\w*|incendiat\w*|motore\s+(?:rotto|fuso|da\s+(?:rifare|sostituire|cambiare))|'
           r'(?:guasto|problemi|rottura)\s+(?:al\s+|del\s+)?(?:motore|cambio)|non\s+marciante|'
           r'batteria\s+(?:alta\s+tensione|trazione).{0,20}(?:danneggiat\w*|guast\w*)|'
           r'gravemente\s+incidentat\w*|uso\s+ricambi)\b',
 'non_severe':r'\b(?:grandin\w*|ammaccatur\w*|ammaccat\w*|'
              r'(?:paraurti|parafango|portiera|cofano|carrozzeria|lamiera).{0,30}(?:da\s+(?:riparare|sistemare|verniciare|sostituire)|danneggiat\w*|rott\w*|crepa)|'
              r'(?:dann\w*|crepa|riparazione).{0,25}(?:paraurti|parafango|portiera|carrozzeria)|'
              r'carrozzeria\s+scolorita)\b',
 'minimal':r'\b(?:graffi\w*|lievi\s+segni|piccol[ie]\s+segni|segni\s+superficiali|'
           r'lievi\s+ammaccature|piccol[ae]\s+ammaccatur\w*)\b'}


def signals(text,pattern):
    found=[]
    for match in re.finditer(pattern,text,re.I):
        prefix=text[max(0,match.start()-40):match.start()]
        phrase=match[0]
        if re.search(r'\b(?:non|no|nessun[oa]?|senza|mai)\s+(?:(?:e|è|stata|stato|sono|ha|avuto)\s+)*$',prefix,re.I):continue
        if re.search(r'\b(?:non|mai)\s+(?:esplos|scoppi|apert|attivat)',phrase,re.I):continue
        found.append(dict(excerpt=phrase,origin='seller_text',status='declared'))
    return found


def classify(p):
    text=' '.join(str(p.get(k) or '') for k in ('title','description'))
    source=p.get('damage_source_claims') or {}
    current=(source.get('rawData.condition.damage') or {}).get('isCurrentlyDamaged')
    declared=source.get('damageConditions')
    if isinstance(declared,list):text+=' '+' '.join(str(v) for v in declared)
    severe=signals(text,PATTERNS['severe'])
    structured=[i for i in p.get('damage_indicators') or [] if i in ('structural','airbags','fire','flood','high_voltage_battery')]
    if p.get('damage_severity')=='severe' or structured or severe:
        category='severe';evidence=severe+[dict(origin='structured',indicator=i) for i in structured]
    else:
        minimal=signals(text,PATTERNS['minimal'])
        moderate=signals(text,PATTERNS['non_severe'])
        # "Lievi ammaccature" is minimal; remove the nested generic match.
        moderate=[m for m in moderate if not any(m['excerpt'].casefold() in n['excerpt'].casefold() for n in minimal)]
        if moderate or p.get('damage_severity')=='moderate':category='non_severe';evidence=moderate
        elif minimal or p.get('damage_severity')=='minor':category='minimal';evidence=minimal
        elif p.get('condition')=='damaged' or current is True or source.get('hadAccident') is True or declared or signals(text,r'\b(?:incidentat\w*|sinistrat\w*|danneggiat\w*)\b'):
            category='unknown';evidence=[]
        elif p.get('condition')=='undamaged' or current is False:category='clean';evidence=[]
        else:category='unknown';evidence=[]
    return dict(version=VERSION,category=category,evidence=evidence,
                severity_verified=False,inspection_required=True,
                eligible_for_opportunity_research=category in MIX,
                missing_evidence=['structural_and_restraint_inspection','mechanical_condition','complete_repair_scope'])


def quotas(limit):
    raw={k:limit*v for k,v in MIX.items()}
    result={k:int(v) for k,v in raw.items()}
    for key in sorted(raw,key=lambda k:raw[k]-result[k],reverse=True)[:limit-sum(result.values())]:result[key]+=1
    return result


def feasibility(p,price_context):
    """Necessary cost budget, not a net forecast: unverified costs stay unknown."""
    from .margin_policy import minimum_net_margin_eur
    reference=price_context['asking_low_eur']
    # Match the existing non-severe final policy's 10%+5% adverse resale scenario.
    exit_ceiling=reference*85//100
    reserve=750
    required=minimum_net_margin_eur(p['price_eur'])
    budget=exit_ceiling-p['price_eur']-reserve-required
    confidence=price_context.get('confidence','low')
    weight={'high':1.,'medium':.75,'low':.5}[confidence]
    return dict(reference_eur=reference,adverse_resale_ceiling_eur=exit_ceiling,
                resale_stress_percent=15,minimum_reserve_eur=reserve,
                minimum_required_net_margin_eur=required,
                remaining_cost_budget_eur=budget,passes_necessary_budget=budget>0,
                priority_score=round(max(0,budget)/p['price_eur']*weight,6),
                confidence_weight=weight,net_margin_eur=None,costs_verified=False,
                basis='necessary_budget_before_unverified_costs',
                unresolved_costs=['repairs','transfer','transport','preparation','warranty','taxes','fees','holding'],
                repaired_history_resale_verified=False,buy_recommendation=False)
