"""Native free-space regression for actual bundle terminals and wide turns."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.controller_bundles import extend_controller_bundles
from smilepcb.keyboard_routing.collector_peel import compress
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.interface_launches import reserve_launches,publish_launches,current_launches
from smilepcb.keyboard_routing.collector_capacity import groups_for
from smilepcb.keyboard_routing.native_graph import copper_graph

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);owners=[];report={}
def make(via_backed,staggered=False,nonuniform=False):
 b=p.BOARD();b.SetCopperLayerCount(2);rules=b.GetDesignSettings();owners.extend([b,rules])
 rules.m_MinClearance=150000;rules.m_TrackMinWidth=150000;rules.m_MinThroughDrill=200000;rules.m_ViasMinSize=500000;rules.m_ViasMinAnnularWidth=150000
 for a,z in zip([(0,0),(40000000,0),(40000000,40000000),(0,40000000)],[(40000000,0),(40000000,40000000),(0,40000000),(0,0)]):
  edge=p.PCB_SHAPE(b);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetLayer(p.Edge_Cuts);edge.SetStart(p.VECTOR2I(*a));edge.SetEnd(p.VECTOR2I(*z));edge.SetWidth(50000);b.Add(edge)
 face=p.B_Cu if via_backed else p.F_Cu;chip=p.FOOTPRINT(b);chip.SetReference('U_MCU');chip.SetPosition(p.VECTOR2I(26000000,22000000));chip.SetLayer(face);b.Add(chip)
 def track(a,z,net,layer):
  t=p.PCB_TRACK(b);t.SetNet(b.FindNet(net));t.SetLayer(layer);t.SetWidth(150000);t.SetStart(p.VECTOR2I(*a));t.SetEnd(p.VECTOR2I(*z));b.Add(t);return t.m_Uuid.AsString()
 ports=[]
 for i in range(9):
  net='signal-'+str(i);b.Add(p.NETINFO_ITEM(b,net));y=20000000+i*500000+(i//3)*250000 if nonuniform else 20000000+i*500000
  pad=p.PAD(chip);pad.SetNumber(str(i+1));pad.SetNet(b.FindNet(net));pad.SetAttribute(p.PAD_ATTRIB_SMD);pad.SetShape(p.PAD_SHAPE_RECT);pad.SetSize(p.VECTOR2I(1500000,300000));layers=p.LSET();layers.AddLayer(face);pad.SetLayerSet(layers);pad.SetPosition(p.VECTOR2I(22000000,y));chip.Add(pad)
  tip=(18000000+(i%3)*500000 if staggered else 18000000,y);start=(22000000,y);added=[];via_id=None;via_nm=None
  if via_backed:
   via_nm=(20000000+(i%2)*500000,y);added.append(track(start,via_nm,net,face));v=p.PCB_VIA(b);v.SetNet(b.FindNet(net));v.SetPosition(p.VECTOR2I(*via_nm));v.SetWidth(500000);v.SetDrill(200000);v.SetViaType(p.VIATYPE_THROUGH);v.SetLayerPair(p.F_Cu,p.B_Cu);b.Add(v);via_id=v.m_Uuid.AsString();added.append(via_id);start=via_nm
  added.append(track(start,tip,net,p.F_Cu))
  ports.append(dict(net=net,pad_ids=[pad.m_Uuid.AsString()],added_ids=added,kind='via' if via_backed else 'same_layer',via_id=via_id,via_nm=via_nm,terminal_id=added[-1],exit_nm=tip,heading_origin_nm=start,exit_layer=p.F_Cu,bank=['U_MCU',0,-1],axis=0,sign=-1,profile=None))
 return b,ports

with generation_profile('dsn-process-v2'):
 for case in ('wide_same_layer','via_track','staggered_via_rail','nonuniform_via_rail','composed_via_carrier'):
  b,ports=make(case!='wide_same_layer',case.endswith('_rail') or case=='composed_via_carrier',case=='nonuniform_via_rail');before=snapshot(b);original={q['net']:dict(q) for q in ports};deadline=time.monotonic()+15
  with reserve_launches(ports):
   if case.endswith('_rail') or case=='composed_via_carrier':
    advanced,result=compress(b,ports,[q['net'] for q in ports],deadline=deadline,preserve_portal_pitch=True)
   else:
    result=extend_controller_bundles(b,ports,deadline=deadline,targets={q['net']:(10000000,5000000) for q in ports});advanced=result['ports']
   alignment_one_rail=None;capacity_groups=None;inventory_exact=None
   if case=='composed_via_carrier' and result['accepted']:
    alignment_one_rail=len({q['exit_nm'][0] for q in advanced})==1
    g=copper_graph(b,{q['net'] for q in ports},deadline);publish_launches(advanced,g)
    result=extend_controller_bundles(b,advanced,deadline=deadline,targets={q['net']:(10000000,5000000) for q in ports});advanced=result['ports']
    g=copper_graph(b,{q['net'] for q in ports},deadline)
    if result['accepted']:publish_launches(advanced,g)
    cfg={'parts':[{'ref':'U_MCU','nets':{str(i+1):q['net'] for i,q in enumerate(ports)}}],'mcu_gpio_pads':{'P'+str(i):str(i+1) for i in range(len(ports))},'layout':{'gpio':{'rows':[],'cols':['P'+str(i) for i in range(len(ports))]}}}
    capacity_groups=groups_for(b,cfg,list(current_launches()));inventory_exact={q['net']:q for q in current_launches()}=={q['net']:q for q in advanced}
   live=snapshot(b);g=copper_graph(b,{q['net'] for q in ports},deadline);ids={q['item'].m_Uuid.AsString():i for i,q in enumerate(g['nodes'])}
   contacts=all(g['find'](ids[u])==g['find'](ids[q['terminal_id']]) for q in advanced for u in q['pad_ids'])
   if result['accepted'] and contacts:publish_launches(advanced,g)
   report[case]=dict(alignment_one_rail=alignment_one_rail,capacity_groups=capacity_groups,inventory_exact=inventory_exact,accepted=result['accepted'],groups=result.get('groups'),paths=result.get('paths'),result=result,north=all(q['exit_nm'][0]==q['heading_origin_nm'][0] and q['exit_nm'][1]<q['heading_origin_nm'][1] for q in advanced),one_rail=len({q['exit_nm'][0] for q in advanced})==1,pitch_nm=sorted(q['exit_nm'][1] for q in advanced),tangent_exact=sorted(q['exit_nm'][1] for q in advanced)==sorted(q['exit_nm'][1] for q in ports),source_exact=all(live['tracks'].get(u)==v for u,v in before['tracks'].items()),footprints_exact=live['footprints']==before['footprints'],contacts_exact=contacts,via_metadata_exact=all((q['kind'],q['via_id'],q['via_nm'])==(original[q['net']]['kind'],original[q['net']]['via_id'],original[q['net']]['via_nm']) for q in advanced))
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
