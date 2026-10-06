"""Execute professional parts/labor checks against explicitly reviewed evidence."""
from .contracts import bounded_cost
from .professional import dossier, document, result


def execute(raw, target, as_of):
    inspection = raw.get('inspection')
    blockers, parts, operations = [], [], []
    required = inspection.get('required_repairs') if isinstance(inspection, dict) else None
    if not isinstance(required, list):
        return result('repair_planning', ['Complete inspected repair scope required'],
                      parts=[], operations=[], effective_minutes=None, labor_cost_cents=None)
    if not required:
        valid = (isinstance(inspection, dict) and inspection.get('verified') is True
                 and inspection.get('complete') is True and target is not None
                 and inspection.get('vehicle_id') == target.vehicle_id)
        return result('repair_planning', [] if valid else ['Reviewed inspection required for zero repairs'],
                      parts=[], operations=[], effective_minutes=0 if valid else None,
                      labor_cost_cents=0 if valid else None, basis='documented_no_repairs_only')
    parts_plan = dossier(raw).get('parts_plan', [])
    labor_plan = dossier(raw).get('labor_plan', [])
    if not isinstance(parts_plan, list) or len(parts_plan)>100:
        blockers.append('Parts plan must be an array of at most 100 reviewed lines'); parts_plan=[]
    if not isinstance(labor_plan, list) or len(labor_plan)>100:
        blockers.append('Labor plan must be an array of at most 100 reviewed operations'); labor_plan=[]
    seen = set()
    for part in parts_plan:
        try:
            error = document(part, target, as_of)
            if error:
                raise ValueError(error)
            if part.get('repair_id') not in required or not isinstance(part.get('id'),str) or part['id'] in seen:
                raise ValueError('Part requires a distinct ID and diagnosed repair reference')
            if type(part.get('quantity')) is not int or not 1<=part['quantity']<=100:
                raise ValueError('Positive part quantity required')
            if not part.get('part_number') or part.get('exact_fitment_confirmed') is not True:
                raise ValueError('Exact code and compatible variant required')
            offers = part.get('offers')
            if not isinstance(offers,list) or not 1<=len(offers)<=30:
                raise ValueError('Reviewed purchasable offers required')
            accepted, rejected = [], []
            for offer in offers:
                try:
                    error = document(offer, target, as_of, maximum_days=7)
                    if error: raise ValueError(error)
                    if offer.get('fitment_confirmed') is not True or offer.get('available') is not True:
                        raise ValueError('Offer compatibility or availability unresolved')
                    if offer.get('part_number')!=part['part_number']:
                        raise ValueError('Offer part code mismatch')
                    if offer.get('tier') not in ('original','aftermarket') or offer.get('condition') not in ('new','used','remanufactured'):
                        raise ValueError('Offer tier/condition required')
                    if not offer.get('seller') or offer.get('quantity_covered') != part['quantity']:
                        raise ValueError('Seller and cost for the entire required quantity needed')
                    if offer.get('shipping_included') is not True or offer.get('deposit_included') is not True:
                        raise ValueError('All-in delivery and deposit coverage required')
                    low, high = bounded_cost(offer,as_of)
                    if high<=0: raise ValueError('Positive purchasable offer required')
                    accepted.append(dict(seller=offer['seller'], part_number=offer['part_number'],
                                         tier=offer['tier'], condition=offer['condition'],
                                         low_cents=low, high_cents=high, evidence_url=offer['evidence_url']))
                except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as error:
                    rejected.append(dict(reason=str(error)))
            if not accepted: raise ValueError('No compatible, complete, available offer')
            accepted.sort(key=lambda x:x['high_cents'])
            seen.add(part['id'])
            parts.append(dict(id=part['id'], repair_id=part['repair_id'], quantity=part['quantity'],
                              cheapest_all_in=accepted[0], alternatives=accepted[1:], rejected_offers=rejected,
                              alternatives_are_separate_condition_tiers=True))
        except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as error:
            blockers.append('Parts plan: '+str(error))
    if not parts:
        blockers.append('Complete verified parts/consumables plan required for repairs')
    seen_operations = set()
    groups = {}
    for operation in labor_plan:
        try:
            error=document(operation,target,as_of)
            if error: raise ValueError(error)
            if not isinstance(operation.get('id'),str) or operation['id'] in seen_operations or operation.get('repair_id') not in required:
                raise ValueError('Operation ID and diagnosed repair reference required')
            low,high=operation.get('low_minutes'),operation.get('high_minutes')
            if type(low) is not int or type(high) is not int or not 0<low<=high<=60000:
                raise ValueError('Sourced positive operation time range required')
            if not operation.get('method') or operation.get('consumables_included') is not True:
                raise ValueError('Work method and consumables scope required')
            seen_operations.add(operation['id'])
            operations.append(dict(id=operation['id'],repair_id=operation['repair_id'],low_minutes=low,
                                   high_minutes=high,evidence_url=operation['evidence_url']))
            if operation.get('shared_disassembly_group'):
                groups.setdefault(operation['shared_disassembly_group'],[]).append(operations[-1])
        except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as error:
            blockers.append('Labor plan: '+str(error))
    if set(x['repair_id'] for x in operations)!=set(required):
        blockers.append('Every diagnosed repair must have a documented labor operation')
    credits=dossier(raw).get('labor_overlap',[])
    if not isinstance(credits,list): credits=[];blockers.append('Labor overlap evidence must be an array')
    applied,seen_groups=[],set()
    for credit in credits:
        try:
            error=document(credit,target,as_of)
            if error:raise ValueError(error)
            group=credit['group']; members=groups.get(group,[])
            if group in seen_groups or len(members)<2:raise ValueError('Overlap requires a distinct shared operation group')
            amount=credit['minutes']
            # Shared work may never exceed the smallest operation; only upper cost planning uses it.
            if type(amount) is not int or not 0<=amount<=min(x['low_minutes'] for x in members):
                raise ValueError('Overlap reduction exceeds evidenced operation time')
            if set(credit.get('operation_ids',[]))!=set(x['id'] for x in members):
                raise ValueError('Overlap must identify the exact operation group')
            applied.append(dict(group=group,minutes=amount));seen_groups.add(group)
        except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as error:
            blockers.append('Labor overlap: '+str(error))
    if any(len(members)>1 and group not in seen_groups for group,members in groups.items()):
        blockers.append('Shared disassembly needs a reviewed overlap decision; do not sum blindly')
    effective=sum(x['high_minutes'] for x in operations)-sum(x['minutes'] for x in applied) if operations and not blockers else None
    coverage=dossier(raw).get('repair_plan_review')
    error=document(coverage,target,as_of)
    if error or coverage.get('parts_and_consumables_complete') is not True or coverage.get('labor_complete') is not True:
        blockers.append('Review complete parts, consumables and labor scope')
    quotes=raw.get('repair_quotes',[])
    try:
        for repair_id in required:
            parts_high=sum(x['cheapest_all_in']['high_cents'] for x in parts if x['repair_id']==repair_id)
            matching=[x for x in quotes if x.get('repair_id')==repair_id]
            if len(matching)!=1 or bounded_cost(matching[0],as_of)[1]<parts_high:
                blockers.append('Complete repair quote understates reviewed parts: '+repair_id)
    except (ValueError,KeyError,TypeError,AttributeError,OverflowError):
        blockers.append('Complete repair quote reconciliation unavailable')
    return result('repair_planning',blockers,parts=parts,operations=operations,
                  overlap_decisions=applied,effective_minutes=effective,labor_cost_cents=None,
                  hourly_rate_assumed=False,complete_quote_controls_still_required=True)
