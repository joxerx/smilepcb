"""Pure explicit compiled-core import bindings; Native proof is caller-owned."""
from copy import deepcopy
import hashlib
import json

LEGACY_REPLACEMENTS={'U_MCU','C_VDD1','C_VDD2','C_IO2','C_VDDA','C_VBAT','C_BULK','C_NRST'}
COMPILED_TARGETS={'U_MCU',*('C_CORE'+str(i) for i in range(1,8)),'R_BOOT','R_NRST'}
COMPILED_REPLACEMENTS=LEGACY_REPLACEMENTS|{'R_BOOT','R_NRST'}


def _unique(values,label):
    if any(not isinstance(v,str) or not v for v in values) or len(values)!=len(set(values)):
        raise ValueError('Missing or duplicate '+label)


def _no_connect(model):
    pads=model.get('no_connect',[])
    if not isinstance(pads,list):raise ValueError('Model no_connect must be a list')
    _unique(pads,'model no_connect pad')
    if not set(pads)<=set(model.get('nets',{})):raise ValueError('Model no_connect names a pad outside its model')
    return frozenset(pads)


def core_import_spec(cfg,descriptor,source,*,source_sha256,copper_nets=()):
    """Validate actual source/model inventory, never a claimed compiler proof.

    No descriptor keeps the legacy eight-part recipe and performs no new
    validation of that path. Compiled sources already use target net names.
    """
    if descriptor is None:
        return dict(compiled=False,source_mcu_ref='U1',ref_map={'U1':'U_MCU',**{'C'+str(i):'C_CORE'+str(i) for i in range(1,8)}},replace_refs=set(LEGACY_REPLACEMENTS))
    if not isinstance(descriptor,dict) or descriptor.get('schema')!=1:raise ValueError('Explicit compiled core schema1 required')
    declared=descriptor.get('source_sha256')
    if not isinstance(declared,str) or len(declared)!=64 or any(c not in '0123456789abcdef' for c in declared) or declared!=source_sha256:
        raise ValueError('Compiled core source SHA differs')
    raw=cfg.get('parts',[]);raw_refs=[q.get('ref') for q in raw];_unique(raw_refs,'raw model reference')
    if any(ref.startswith('C_CORE') for ref in raw_refs):raise ValueError('Compiled import requires original raw model')
    raw_by_ref={q['ref']:q for q in raw}
    replacements=descriptor.get('replace_refs',[])
    if not isinstance(replacements,list):raise ValueError('Explicit replacement references required')
    _unique(replacements,'replacement reference')
    if set(replacements)!=COMPILED_REPLACEMENTS or not COMPILED_REPLACEMENTS<=set(raw_by_ref):raise ValueError('Compiled replacement inventory differs from exact raw ten-part set')
    if any(raw_by_ref[ref].get('region')!='controller' for ref in replacements):raise ValueError('Replacement is not a controller component')
    rows=descriptor.get('models',[])
    if not isinstance(rows,list) or len(rows)!=10:raise ValueError('Exactly ten explicit source/target models required')
    _unique([q.get('source_ref') for q in rows],'source model reference');_unique([q.get('target_ref') for q in rows],'target model reference')
    if {q['source_ref'] for q in rows}!=COMPILED_TARGETS or {q['target_ref'] for q in rows}!=COMPILED_TARGETS:raise ValueError('Compiled model inventory differs from exact ten-part set')
    if not isinstance(source,list) or len(source)!=10:raise ValueError('Actual compiled source requires exactly ten footprints')
    _unique([q.get('ref') for q in source],'actual source reference')
    if {q['ref'] for q in source}!=COMPILED_TARGETS:raise ValueError('Actual source reference inventory differs')
    actual={q['ref']:q for q in source};all_ids=[];models={};net_map={};ref_map={}
    for row in rows:
        old,ref=row['source_ref'],row['target_ref'];model=row.get('model')
        if old!=ref or not isinstance(model,dict) or model.get('ref')!=ref or model.get('region')!='controller':raise ValueError('Explicit source/target model binding differs')
        if any(not isinstance(model.get(k),str) or not model[k] for k in ('symbol','value')):raise ValueError('Explicit symbol and value required')
        nets=model.get('nets')
        if not isinstance(nets,dict) or not nets or any(not isinstance(k,str) or not k or not isinstance(v,str) or not v for k,v in nets.items()):raise ValueError('Explicit numbered model pad nets required')
        no_connect=_no_connect(model)
        if ref in {'U_MCU','R_BOOT','R_NRST'}:
            if any(model[k]!=raw_by_ref[ref].get(k) for k in ('symbol','value','nets')):raise ValueError('Compiled MCU/resistor raw binding changed')
            if no_connect!=_no_connect(raw_by_ref[ref]):raise ValueError('Compiled MCU/resistor raw no_connect binding changed')
        footprint=actual[old];pads=footprint.get('pads',[]);_unique([q.get('number') for q in pads],'actual numbered source pad')
        if {q['number']:q.get('net') for q in pads}!=nets or footprint.get('value')!=model['value']:raise ValueError('Actual source numbered pad/value bindings differ from explicit model')
        all_ids.extend([footprint.get('uuid'),*(q.get('uuid') for q in pads)])
        models[old]=deepcopy(model);ref_map[old]=ref;net_map.update({net:net for net in nets.values()})
    _unique(all_ids,'actual source footprint/pad identity')
    if not set(copper_nets)<=set(net_map):raise ValueError('Compiled copper has no explicit source pad net binding')
    return dict(compiled=True,source_mcu_ref='U_MCU',ref_map=ref_map,models=models,net_map=net_map,replace_refs=set(replacements),
        source_inventory=deepcopy(actual),source_sha256=declared,descriptor_sha256=hashlib.sha256(json.dumps(descriptor,sort_keys=True).encode()).hexdigest(),compiler_evidence=deepcopy(descriptor.get('proof')))


def regenerated_identity_record(source,target_ref,target_fp_id,target_pad_ids):
    numbers=[q['number'] for q in source['pads']];_unique(numbers,'source pad number')
    if set(target_pad_ids)!=set(numbers):raise ValueError('Regenerated pad inventory differs')
    new_ids=[target_fp_id,*target_pad_ids.values()];_unique(new_ids,'regenerated footprint/pad identity')
    old_ids={source['uuid'],*(q['uuid'] for q in source['pads'])}
    if old_ids&set(new_ids):raise ValueError('Imported identity was not regenerated')
    return dict(source_ref=source['ref'],source_fp_id=source['uuid'],target_ref=target_ref,target_fp_id=target_fp_id,
        pads=[dict(number=q['number'],source_pad_id=q['uuid'],target_pad_id=target_pad_ids[q['number']]) for q in sorted(source['pads'],key=lambda q:q['number'])])
