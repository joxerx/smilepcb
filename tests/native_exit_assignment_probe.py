"""Final physical lane order must change only safe, owned pre-compose GPIO."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.controller_ports import route_controller_ports
from smilepcb.keyboard_routing.controller_bundles import extend_controller_bundles
from smilepcb.keyboard_routing.generation import generation_profile
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
def make(include_column=False):
 b=p.BOARD();b.SetCopperLayerCount(2);r=b.GetDesignSettings();r.m_MinClearance=150000;r.m_TrackMinWidth=150000;r.m_MinThroughDrill=300000;r.m_ViasMinSize=600000;r.m_ViasMinAnnularWidth=150000
 for net in ('alpha','beta','gamma','column','+3V3'):b.Add(p.NETINFO_ITEM(b,net))
 for a,z in zip([(0,0),(40000000,0),(40000000,40000000),(0,40000000)],[(40000000,0),(40000000,40000000),(0,40000000),(0,0)]):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetLayer(p.Edge_Cuts);e.SetStart(p.VECTOR2I(*a));e.SetEnd(p.VECTOR2I(*z));e.SetWidth(50000);b.Add(e)
 def fp(ref,position):
  f=p.FOOTPRINT(b);f.SetReference(ref);f.SetPosition(p.VECTOR2I(*position));f.SetLayer(p.F_Cu);b.Add(f);return f
 def pad(f,number,net,x,y):
  d=p.PAD(f);d.SetNumber(number);d.SetNet(b.FindNet(net));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(1500000,300000));ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);d.SetPosition(p.VECTOR2I(x,y));f.Add(d);return d
 chip=fp('U_MCU',(16000000,15000000))
 for i,net in enumerate(('alpha','beta','gamma')):pad(chip,str(i+1),net,20000000,14500000+i*500000)
 pad(chip,'1','alpha',20050000,14500000)
 pad(chip,'4','column',15000000,11000000);pad(chip,'5','+3V3',12000000,15000000)
 field=fp('keys',(30000000,5000000))
 for i,net in enumerate(('alpha','beta','gamma')):pad(field,str(i+1),net,32000000,3000000+i*1500000)
 pad(field,'4','column',8000000,8000000)
 t=p.PCB_TRACK(b);t.SetNet(b.FindNet('+3V3'));t.SetLayer(p.B_Cu);t.SetWidth(200000);t.SetStart(p.VECTOR2I(3000000,3000000));t.SetEnd(p.VECTOR2I(5000000,3000000));t.SetLocked(True);b.Add(t)
 cfg={'parts':[{'ref':'U_MCU','region':'controller','nets':{'1':'alpha','2':'beta','3':'gamma','4':'column','5':'+3V3'},'no_connect':[]},{'ref':'keys','region':'matrix','nets':{'1':'alpha','2':'beta','3':'gamma','4':'column'}}], 'mcu_gpio_pads':{'A0':'1','A1':'2','A2':'3','B0':'4'},'layout':{'gpio':{'rows':['A0','A1','A2'],'cols':['B0']},'rgb':False},'plane_nets':[],'qmk':{'matrix_pins':{'rows':['A0','A1','A2'],'cols':['B0']}}}
 with generation_profile('dsn-safe-v1'):
  result=route_controller_ports(b,controller_refs={'U_MCU'},mcu_refs={'U_MCU'},include_nets={'alpha','beta','gamma','column'} if include_column else {'alpha','beta','gamma'},access_mode='prefer_same_layer',continuation_nm=500000,deadline=time.monotonic()+15)
  assert result['accepted'],result
  bundle=extend_controller_bundles(b,result['ports'],deadline=time.monotonic()+10,targets={n:(32000000,32000000) for n in ('alpha','beta','gamma')});assert bundle['accepted'],bundle
 return b,cfg,bundle['ports']
report={}
for case in ('valid','unowned','locked'):
 b,cfg,ports=make()
 if case=='unowned':
  q=next(q for q in ports if q['net']=='alpha');a=q['exit_nm'];t=p.PCB_TRACK(b);t.SetNet(b.FindNet('alpha'));t.SetLayer(q['exit_layer']);t.SetWidth(150000);t.SetStart(p.VECTOR2I(*a));t.SetEnd(p.VECTOR2I(a[0],a[1]+1000000));b.Add(t)
 if case=='locked':
  uid=next(q for q in ports if q['net']=='alpha')['added_ids'][0];next(t for t in b.GetTracks() if t.m_Uuid.AsString()==uid).SetLocked(True)
 before=snapshot(b);error=None;updated=None;result={}
 try:
  from smilepcb.keyboard_routing.gpio import apply_exit_assignment
  updated,result=apply_exit_assignment(b,cfg,ports,deadline=time.monotonic()+15)
 except Exception as e:error=type(e).__name__+': '+str(e)
 after=snapshot(b);report[case]={'error':error,'rows':updated['layout']['gpio']['rows'] if updated else None,'qmk_rows':updated['qmk']['matrix_pins']['rows'] if updated else None,'cols':updated['layout']['gpio']['cols'] if updated else None,'unchanged':before==after,'result':result,'all_pin1_aliases_remapped':all(d.GetNetname()=='gamma' for f in b.GetFootprints() if f.GetReference()=='U_MCU' for d in f.Pads() if d.GetNumber()=='1') if updated else False}
for case in ('matrix_valid','matrix_default','matrix_incomplete','matrix_unowned','matrix_locked','matrix_field_connected','matrix_omitted_alias','matrix_disconnected_claim'):
 b,cfg,ports=make(include_column=True)
 mapping={'alpha':'column','column':'alpha','beta':'beta','gamma':'gamma'}
 if case=='matrix_incomplete':
  ports=[q for q in ports if q['net']!='column'];mapping={n:n for n in ('alpha','beta','gamma')}
 if case=='matrix_unowned':
  q=next(q for q in ports if q['net']=='alpha');a=q['exit_nm'];t=p.PCB_TRACK(b);t.SetNet(b.FindNet('alpha'));t.SetLayer(q['exit_layer']);t.SetWidth(150000);t.SetStart(p.VECTOR2I(*a));t.SetEnd(p.VECTOR2I(a[0],a[1]+1000000));b.Add(t)
 if case=='matrix_locked':
  uid=next(q for q in ports if q['net']=='alpha')['added_ids'][0];next(t for t in b.GetTracks() if t.m_Uuid.AsString()==uid).SetLocked(True)
 if case=='matrix_field_connected':
  q=next(q for q in ports if q['net']=='alpha');d=next(d for f in b.GetFootprints() if f.GetReference()=='keys' for d in f.Pads() if d.GetNetname()=='alpha');d.SetPosition(p.VECTOR2I(*q['exit_nm']))
 if case=='matrix_omitted_alias':
  chip=next(f for f in b.GetFootprints() if f.GetReference()=='U_MCU');old=next(d for d in chip.Pads() if d.GetNumber()=='1');alias=p.PAD(chip);alias.SetNumber('1');alias.SetNet(b.FindNet('alpha'));alias.SetAttribute(p.PAD_ATTRIB_SMD);alias.SetShape(p.PAD_SHAPE_RECT);alias.SetSize(old.GetSize());alias.SetLayerSet(old.GetLayerSet());alias.SetPosition(p.VECTOR2I(5000000,30000000));chip.Add(alias)
 if case=='matrix_disconnected_claim':
  q=next(q for q in ports if q['net']=='alpha');t=p.PCB_TRACK(b);t.SetNet(b.FindNet('alpha'));t.SetLayer(q['exit_layer']);t.SetWidth(150000);t.SetStart(p.VECTOR2I(5000000,5000000));t.SetEnd(p.VECTOR2I(6000000,5000000));b.Add(t);q['added_ids'].append(t.m_Uuid.AsString())
 before=snapshot(b);cfg_before=json.loads(json.dumps(cfg));error=None;updated=None;receipt={}
 try:
  from smilepcb.keyboard_routing.gpio import apply_exit_assignment
  kw={} if case=='matrix_default' else {'assignment_scope':'matrix'}
  updated,receipt=apply_exit_assignment(b,cfg,ports,deadline=time.monotonic()+15,net_mapping=mapping,**kw)
 except Exception as e:error=type(e).__name__+': '+str(e)
 after=snapshot(b)
 report[case]={'error':error,'unchanged':before==after,'input_model_unchanged':cfg==cfg_before,
  'rows':updated['layout']['gpio']['rows'] if updated else None,'cols':updated['layout']['gpio']['cols'] if updated else None,
  'qmk_pins':updated['qmk']['matrix_pins'] if updated else None,'receipt':receipt,
  'fixed_functions_unchanged':all(d.GetNetname()=='+3V3' for f in b.GetFootprints() if f.GetReference()=='U_MCU' for d in f.Pads() if d.GetNumber()=='5'),
  'aliases_remapped':all(d.GetNetname()=='column' for f in b.GetFootprints() if f.GetReference()=='U_MCU' for d in f.Pads() if d.GetNumber()=='1') if updated else False}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
