"""Source-bound field recovery. Seller evidence never becomes physical verification."""
import re
import unicodedata
from decimal import Decimal, InvalidOperation

VERSION='vehicle-identity-evidence-v2'
FIELDS=('engine_code','engine_name','displacement_cc','power_kw','power_hp','fuel',
        'transmission','drivetrain','body_type','generation','trim')
SOURCE_PATHS={
    'engine_code':('engineCode','rawData.engine.engineCode.formatted'),
    'engine_name':('motorTypeName','rawData.engine.motorType.formatted'),
    'displacement_cc':('rawCylinderCapacity','rawDisplacementInCCM','engineDisplacementInCCM'),
    'power_hp':('rawPowerInHp',), 'power_kw':('rawPowerInKw',),
    'fuel':('fuelCategory.formatted','fuel','primaryFuel.formatted'),
    'transmission':('transmissionType','transmission'),
    'drivetrain':('driveTrain',), 'body_type':('bodyType',),
    'generation':('rawData.classification.modelGeneration.formatted',),
    'trim':('rawData.classification.trimLine.formatted',)}
DAMAGE_PATHS=('hadAccident','damageConditions','rawData.condition.damage','isCurrentlyDamaged')


def text(value):
    if not isinstance(value,str):return None
    value=' '.join(''.join(c for c in unicodedata.normalize('NFKD',value.casefold())
                         if not unicodedata.combining(c)).split())
    return value if value not in ('','unknown','n/a','-','sconosciuto') else None


def normalize_field(field,value):
    if field in ('power_kw','power_hp','displacement_cc'):
        if field=='displacement_cc' and isinstance(value,str):
            formatted=re.fullmatch(r'\s*(\d{1,2}(?:[ .]\d{3})|\d{3,5})\s*(?:cm³|cm3|cc)\s*',value,re.I)
            if formatted:
                value=re.sub(r'[ .]','',formatted[1])
        try:
            if isinstance(value,bool):return None
            n=Decimal(str(value))
            maximum=20000 if field=='displacement_cc' else 3000
            if field=='power_kw' and n.is_finite() and 0<n<=maximum:
                return int(n) if n==int(n) else float(n)
            return int(n) if n.is_finite() and n==int(n) and 0<n<=maximum else None
        except (ValueError,TypeError,InvalidOperation,OverflowError):return None
    value=text(value)
    if value is None:return None
    aliases={
        'transmission':{'manuale':'manual','cambio manuale':'manual','manual':'manual',
                        'automatico':'automatic','cambio automatico':'automatic','automatic':'automatic',
                        'semiautomatico':'semi_automatic','cambio semiautomatico':'semi_automatic'},
        'fuel':{'benzina':'petrol','gasoline':'petrol','petrol':'petrol','gasolio':'diesel',
                'diesel':'diesel','metano':'cng','gpl':'lpg','elettrica':'electric','elettrico':'electric',
                'benzina/metano':'petrol_cng','benzina/gas':'petrol_lpg'},
        'drivetrain':{'anteriore':'fwd','trazione anteriore':'fwd','posteriore':'rwd',
                      'trazione posteriore':'rwd','integrale':'awd','4x4':'awd','4wd':'awd'},
        'body_type':{'berlina':'sedan','station wagon':'wagon','city car':'city_car',
                     'suv/fuoristrada/pick-up':'suv_offroad_pickup','cabrio':'convertible'}}
    if field=='fuel':
        if re.match(r'^benzina (?:e\d+|\d+)',value):return 'petrol'
        if value in ('gas naturale h','gas naturale l','biogas'):return 'cng'
        if value=='gas di petrolio liquefatto':return 'lpg'
        if re.match(r'^diesel (?:b\d+|\d+)',value):return 'diesel'
    return aliases.get(field,{}).get(value,value)


def at(value,path):
    for part in path.split('.'):
        if not isinstance(value,dict):return None
        value=value.get(part)
    return value


def source_fragment(payload):
    original=payload.get('original')
    if isinstance(original,dict):
        vehicle=original.get('vehicle') or {}
        paths=[path for values in SOURCE_PATHS.values() for path in values]+list(DAMAGE_PATHS)
        return {path:at(vehicle,path) for path in paths}
    return payload.get('identity_source_fields') or {}


def text_claims(payload):
    result=[]
    for origin in ('version_text','title','description'):
        value=payload.get(origin)
        if not isinstance(value,str):continue
        for field,pattern in (
            ('power_hp',r'\b(\d{2,4})\s*(?:cv|hp|ps)\b'),
            ('power_kw',r'\b(\d{2,4})\s*kw\b'),
            ('displacement_cc',r'\b(\d{3,5})\s*(?:cc|cm3|cm³)\b'),
            ('engine_code',r'\bcodice\s+motore\s*[:=]?\s*([a-z0-9-]{3,15})\b'),
            ('transmission',r'\bcambio\s+(automatico|manuale|semiautomatico)\b')):
            for match in re.finditer(pattern,value,re.I):
                if field=='power_hp' and (re.search(r'fiscal.{0,20}$',value[max(0,match.start()-35):match.start()],re.I) or re.match(r'\s*fiscal',value[match.end():],re.I)):
                    continue
                if field=='power_kw' and re.search(r'(?:ricarica|colonnina|charger|charging).{0,30}$',value[max(0,match.start()-45):match.start()],re.I):
                    continue
                result.append((field,match[1],origin,match[0]))
    version=payload.get('version_text') or ''
    if isinstance(version,str):
        # Explicit labels only; never infer a generation from registration year.
        if not payload.get('generation'):
            match=re.search(r'\b(VIII|VII|VI|IV|III|II|IX|V|I)(?=\s|20\d{2}|$)',version)
            if match:result.append(('generation',match[1],'version_text',match[0]))
        if not payload.get('trim'):
            for label in ('sport line','m sport','climbing','expression','ambition','lounge','easy','pop','active'):
                if re.search(r'\b'+re.escape(label)+r'\b',version,re.I):
                    result.append(('trim',label,'version_text',label))
    return result


def resolve(payload, *, source_url=None):
    """Return normalized values, every claim, recovery gaps and open conflicts."""
    claims={field:[] for field in FIELDS}
    fragment=source_fragment(payload)
    def add(field,value,path,origin,excerpt=None):
        normalized=normalize_field(field,value)
        if normalized is not None:
            claims[field].append(dict(value=normalized,raw_value=value,source_path=path,
                origin=origin,status='declared',source_url=source_url,excerpt=excerpt))
    for field in FIELDS:
        for path in SOURCE_PATHS[field]:
            if field=='fuel' and path=='primaryFuel.formatted' and normalize_field('fuel',fragment.get('fuelCategory.formatted')):
                continue
            add(field,fragment.get(path),'original.vehicle.'+path,'source_structured')
        previous=payload.get('identity_dossier') or {}
        if not isinstance(payload.get('original'),dict) and not payload.get('identity_source_fields'):
            for claim in previous.get('fields',{}).get(field,{}).get('claims',[]):
                if claim.get('origin')!='normalized_source':
                    add(field,claim.get('raw_value',claim.get('value')),claim.get('source_path'),claim.get('origin'),claim.get('excerpt'))
        add(field,payload.get(field),'payload.'+field,'normalized_source')
    for field,value,path,excerpt in text_claims(payload):add(field,value,path,'seller_text',excerpt)
    for field,items in claims.items():
        claims[field]=list({(i['source_path'],i['value'],i['origin']):i for i in items}.values())
    fields={};values={};conflicts=[]
    for field,items in claims.items():
        chosen=items[0]['value'] if items else None
        distinct=[]
        for item in items:
            value=item['value']
            if field=='transmission' and item['origin']=='seller_text' and value=='automatic' and 'semi_automatic' in distinct:
                continue
            tolerance=2 if field in ('power_hp','power_kw') else 0
            if not any(abs(value-v)<=tolerance if type(value) is int and type(v) is int else value==v for v in distinct):
                distinct.append(value)
        conflict=len(distinct)>1
        if conflict:conflicts.append(dict(field=field,values=distinct,claims=items,resolution='review_required'))
        source_present=any(item['origin']=='source_structured' for item in items)
        existing=normalize_field(field,payload.get(field))
        origins={i['origin'] for i in items if i['origin']!='normalized_source'}
        status='conflict' if conflict else ('consistent' if len(origins)>=2 else 'declared') if items else 'missing'
        recovery=('normalization_gap' if source_present and existing is None else
                  'normalized_value_mismatch' if source_present and existing!=chosen else
                  'present' if existing is not None else 'seller_text_only' if items else 'absent_from_source')
        fields[field]=dict(value=chosen,status=status,recovery_status=recovery,claims=items,
                           verification_required=True)
        if field=='fuel' and fragment.get('primaryFuel.formatted'):
            fields[field]['source_components']=[dict(value=normalize_field('fuel',fragment['primaryFuel.formatted']),raw_value=fragment['primaryFuel.formatted'],source_path='original.vehicle.primaryFuel.formatted',status='declared_component')]
        if chosen is not None:values[field]=chosen
    hp=values.get('power_hp');kw=values.get('power_kw')
    if hp and kw and abs(hp-kw*1.35962)>2:
        conflicts.append(dict(field='power_hp/power_kw',values=[hp,kw],
            resolution='review_required',reason='Declared metric horsepower and kW disagree'))
        fields['power_hp']['status']=fields['power_kw']['status']='conflict'
    return dict(version=VERSION,fields=fields,values=values,conflicts=conflicts,
        status='needs_review' if conflicts else 'source_identity_context',
        usable_for_price_comparison=not conflicts,physical_identity_verified=False,
        required_verification=['vehicle_document_or_vin_for_exact_engine','damage_inspection'],
        missing_fields=[f for f,v in fields.items() if v['value'] is None])


def enrich(payload, *, source_url=None):
    dossier=resolve(payload,source_url=source_url)
    result=dict(payload,**dossier['values'],identity_dossier=dossier)
    fragment=source_fragment(payload)
    damage={path:fragment[path] for path in DAMAGE_PATHS if fragment.get(path) is not None}
    if damage:result['damage_source_claims']=damage
    return result


def compact_source_sql(dialect):
    paths=[path for values in SOURCE_PATHS.values() for path in values]+list(DAMAGE_PATHS)
    # Only named scalars leave PostgreSQL: never transfer equipment/images/original blobs.
    pairs=[]
    for path in paths:
        key="'"+path+"'"
        if dialect=='postgres':
            value="e.payload#>'{original,vehicle,"+path.replace('.',',')+"}'"
        else:
            value="json_extract(e.payload,'$.original.vehicle."+path+"')"
        pairs.extend((key,value))
    return ('jsonb_build_object' if dialect=='postgres' else 'json_object')+'('+','.join(pairs)+')'


def compatible_fields(a,b):
    da=resolve(a);db=resolve(b)
    if da['conflicts'] or db['conflicts']:return False
    for field in FIELDS:
        left=da['values'].get(field);right=db['values'].get(field)
        if left is not None and right is not None and left!=right:return False
    return True


def annotate_verified_fields(dossier,proof,listing,as_of):
    """Attach reviewed document fields only after binding and attestation checks.

    A vehicle-ID attestation without reviewed field values never verifies an engine.
    Document authenticity remains the named reviewer's responsibility.
    """
    from copy import deepcopy
    from .agents.contracts import verified_identity
    result=deepcopy(dossier)
    if not verified_identity(proof,listing,as_of):return result
    if any(proof.get(k)!=getattr(listing,k) for k in ('source','source_id','observed_at')):return result
    if proof.get('evidence_origin') not in ('vehicle_document','vin_report','manufacturer_document'):return result
    fields=proof.get('verified_fields')
    if not isinstance(fields,dict):return result
    for field,value in fields.items():
        if field not in FIELDS:continue
        normalized=normalize_field(field,value)
        record=result['fields'][field]
        if normalized is None or normalized!=record['value'] or record['status']=='conflict':continue
        record['status']='verified';record['verification_required']=False
        record['claims'].append(dict(value=normalized,origin=proof['evidence_origin'],status='verified',
            source_path='identity_evidence.verified_fields.'+field,source_url=proof['evidence_url'],
            verified_by=proof['verified_by'],checked_at=proof['verified_at']))
    result['physical_identity_verified']=True
    result['exact_variant_verified']=all(result['fields'][f]['status']=='verified'
        for f in ('displacement_cc','power_hp','fuel','transmission','generation','trim'))
    return result
