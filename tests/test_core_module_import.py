"""Pure compiled-module import binding contracts; Native admission is separate."""
from copy import deepcopy
import pytest
from smilepcb.stm32.core_module_import import core_import_spec,regenerated_identity_record

TARGETS={'U_MCU',*('C_CORE'+str(i) for i in range(1,8)),'R_BOOT','R_NRST'}
REPLACEMENTS={'U_MCU','C_VDD1','C_VDD2','C_IO2','C_VDDA','C_VBAT','C_BULK','C_NRST','R_BOOT','R_NRST'}


def fixture():
    def model(ref):
        symbol='MCU:part' if ref=='U_MCU' else 'Device:R' if ref.startswith('R_') else 'Device:C'
        nets={'1':'+3V3','2':'GND'}
        if ref=='R_BOOT':nets={'1':'BOOT0','2':'GND'}
        if ref=='R_NRST':nets={'1':'+3V3','2':'NRST'}
        return dict(ref=ref,symbol=symbol,value='10k' if ref.startswith('R_') else '100nF',region='controller',nets=nets,footprint='Parts:body')
    cfg={'parts':[model(ref) for ref in sorted(REPLACEMENTS|{'R_RGB'})]}
    rows=[dict(source_ref=ref,target_ref=ref,model=model(ref)) for ref in sorted(TARGETS)]
    source=[dict(ref=row['source_ref'],uuid='fp-'+row['source_ref'],value=row['model']['value'],pads=[dict(number=number,uuid=row['source_ref']+'-'+number,net=net) for number,net in row['model']['nets'].items()]) for row in rows]
    descriptor=dict(schema=1,source_sha256='a'*64,models=rows,replace_refs=sorted(REPLACEMENTS),proof={'compiler':'evidence only'})
    return cfg,descriptor,source


def test_no_descriptor_retains_legacy_eight_ref_mapping_and_resistor_auxiliaries():
    cfg,_,_=fixture();before=deepcopy(cfg)
    spec=core_import_spec(cfg,None,[],source_sha256='unused')
    assert spec['compiled'] is False and spec['source_mcu_ref']=='U1'
    assert spec['ref_map']=={'U1':'U_MCU',**{'C'+str(i):'C_CORE'+str(i) for i in range(1,8)}}
    assert 'R_BOOT' not in spec['replace_refs'] and 'R_NRST' not in spec['replace_refs'] and cfg==before


def test_compiled_inventory_uses_explicit_resistor_models_and_identity_nets_without_mutation():
    cfg,descriptor,source=fixture();before=deepcopy((cfg,descriptor,source))
    spec=core_import_spec(cfg,descriptor,source,source_sha256='a'*64,copper_nets=['+3V3','NRST'])
    assert spec['compiled'] and spec['source_mcu_ref']=='U_MCU' and len(spec['ref_map'])==10
    assert spec['models']['R_BOOT']['symbol']=='Device:R' and spec['models']['R_NRST']['symbol']=='Device:R'
    assert spec['replace_refs']==REPLACEMENTS and 'R_RGB' not in spec['replace_refs']
    assert all(old==new for old,new in spec['net_map'].items())
    assert spec['compiler_evidence']==descriptor['proof'] and (cfg,descriptor,source)==before


@pytest.mark.parametrize('change',[
    lambda cfg,d,s:d.update(source_sha256='b'*64),
    lambda cfg,d,s:d['replace_refs'].append('R_RGB'),
    lambda cfg,d,s:d['replace_refs'].remove('R_BOOT'),
    lambda cfg,d,s:cfg['parts'].append(deepcopy(next(p for p in cfg['parts'] if p['ref']=='R_BOOT'))),
    lambda cfg,d,s:cfg['parts'].append(dict(ref='C_CORE1')),
    lambda cfg,d,s:s.append(deepcopy(s[0])),
    lambda cfg,d,s:s.pop(),
    lambda cfg,d,s:s[0]['pads'].append(deepcopy(s[0]['pads'][0])),
    lambda cfg,d,s:s[0]['pads'][0].update(net='FOREIGN'),
    lambda cfg,d,s:s[0]['pads'].pop(),
    lambda cfg,d,s:next(r for r in d['models'] if r['target_ref']=='R_BOOT')['model'].update(symbol='Device:C'),
    lambda cfg,d,s:next(r for r in d['models'] if r['target_ref']=='R_NRST')['model']['nets'].update({'2':'GND'}),
    lambda cfg,d,s:next(r for r in d['models'] if r['target_ref']=='U_MCU')['model']['nets'].update({'1':'OTHER'}),
])
def test_compiled_descriptor_rejects_stale_ambiguous_or_rebound_import(change):
    cfg,descriptor,source=fixture();change(cfg,descriptor,source)
    with pytest.raises(ValueError):core_import_spec(cfg,descriptor,source,source_sha256='a'*64)


def test_unknown_source_copper_is_not_accepted_by_opaque_compiler_proof():
    cfg,descriptor,source=fixture();descriptor['proof']={'accepted':True,'waive_foreign':True}
    with pytest.raises(ValueError):core_import_spec(cfg,descriptor,source,source_sha256='a'*64,copper_nets=['FOREIGN'])


def test_regenerated_pad_identity_map_is_explicit_complete_and_fresh():
    _,_,source=fixture();row=next(p for p in source if p['ref']=='R_BOOT')
    record=regenerated_identity_record(row,'R_BOOT','new-fp',{'1':'new-pad1','2':'new-pad2'})
    assert record==dict(source_ref='R_BOOT',source_fp_id='fp-R_BOOT',target_ref='R_BOOT',target_fp_id='new-fp',pads=[dict(number='1',source_pad_id='R_BOOT-1',target_pad_id='new-pad1'),dict(number='2',source_pad_id='R_BOOT-2',target_pad_id='new-pad2')])
    for target_fp,pads in [('fp-R_BOOT',{'1':'n1','2':'n2'}),('new-fp',{'1':'n1'}),('new-fp',{'1':'same','2':'same'}),('new-fp',{'1':'R_BOOT-1','2':'n2'})]:
        with pytest.raises(ValueError):regenerated_identity_record(row,'R_BOOT',target_fp,pads)


@pytest.mark.parametrize('ref',['U_MCU','R_BOOT','R_NRST'])
def test_compiled_models_cannot_add_or_remove_raw_no_connect_obligations(ref):
    cfg,descriptor,source=fixture()
    row=next(r for r in descriptor['models'] if r['target_ref']==ref)
    row['model']['no_connect']=['1']
    with pytest.raises(ValueError,match='no.connect'):core_import_spec(cfg,descriptor,source,source_sha256='a'*64)
    row['model']['no_connect']=[]
    next(p for p in cfg['parts'] if p['ref']==ref)['no_connect']=['1']
    with pytest.raises(ValueError,match='no.connect'):core_import_spec(cfg,descriptor,source,source_sha256='a'*64)


def test_missing_and_empty_no_connect_are_equivalent_and_order_is_not_binding():
    cfg,descriptor,source=fixture()
    for row in descriptor['models']:row['model']['no_connect']=[]
    assert core_import_spec(cfg,descriptor,source,source_sha256='a'*64)['compiled']
    raw=next(p for p in cfg['parts'] if p['ref']=='U_MCU');raw['no_connect']=['1','2']
    next(r for r in descriptor['models'] if r['target_ref']=='U_MCU')['model']['no_connect']=['2','1']
    assert core_import_spec(cfg,descriptor,source,source_sha256='a'*64)['compiled']


@pytest.mark.parametrize('value',[None,'1',('1',),['1','1'],[1],[''],['99']])
def test_explicit_model_rejects_malformed_no_connect_inventory(value):
    cfg,descriptor,source=fixture()
    next(r for r in descriptor['models'] if r['target_ref']=='C_CORE1')['model']['no_connect']=value
    with pytest.raises(ValueError,match='no.connect'):core_import_spec(cfg,descriptor,source,source_sha256='a'*64)
