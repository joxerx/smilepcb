import json,sys,time
from copy import deepcopy
from pathlib import Path
from native_bank_fixture import make
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.gpio import apply_exit_assignment
from smilepcb.keyboard_routing.interface_contract import capture_interfaces,verify_interfaces
from smilepcb.keyboard_routing.port_policy import PortPolicy
from smilepcb.keyboard_routing.native_graph import copper_graph
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);results={}
b,cfg,ports=make();cfg,a=apply_exit_assignment(b,cfg,ports,deadline=time.monotonic()+10);ports=a['ports']
# Add a real via followed by a runway, so its remote tip exists on one face.
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
col=next(q for q in ports if q['net']=='column');a=tuple(col['exit_nm']);origin=tuple(col['heading_origin_nm']);h=tuple((a[i]>origin[i])-(a[i]<origin[i]) for i in (0,1));z=tuple(a[i]+h[i]*2000000 for i in (0,1));other=p.B_Cu if col['exit_layer']==p.F_Cu else p.F_Cu
oracle=LocalRouter(b,compact=True,track_width_nm=150000);oracle.add_via(a,col['net']);via_id=oracle.added[-1];oracle.add_path([a,z],other,col['net']);col['added_ids'].extend(oracle.added);col.update(kind='via',via_id=via_id,via_nm=a,exit_nm=z,exit_layer=other,heading_origin_nm=a,terminal_id=oracle.added[-1])
receipt=capture_interfaces(b,cfg,ports,deadline=time.monotonic()+10)
p.SaveBoard(str(out/'board.kicad_pcb'),b);b=p.LoadBoard(str(out/'board.kicad_pcb'))
# JSON/native roundtrip and schematic path synchronization must be legal.
receipt=json.loads(json.dumps(receipt));chip=next(f for f in b.GetFootprints() if f.GetReference()=='U_MCU');chain=p.KIID_PATH();chain.push_back(p.KIID());chip.SetPath(chain)
results['valid']=verify_interfaces(b,cfg,receipt,deadline=time.monotonic()+10)
policy=PortPolicy(b,ports);graph=copper_graph(b,{q['net'] for q in ports},time.monotonic()+5);q=ports[0]
ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])};root=graph['find'](ids[q['terminal_id']]);indices=[i for i in range(len(graph['nodes'])) if graph['find'](i)==root]
contacts=policy.contacts(graph,indices,(35000000,35000000));a=tuple(q['exit_nm']);h=tuple(a[i]-q['heading_origin_nm'][i] for i in (0,1));g=max(map(abs,h));d=tuple(v//g*1000000 for v in h)
results['policy']={'terminal_only':contacts=={(a,q['exit_layer'])},'forward':policy.accepts_segment(a,tuple(a[i]+d[i] for i in (0,1)),q['exit_layer'],q['net']),'reverse_rejected':not policy.accepts_segment(a,tuple(a[i]-d[i] for i in (0,1)),q['exit_layer'],q['net'])}

# Finite copper and drill annuli must not create a second MCU-side contact.
row=next(q for q in ports if q['kind']=='same_layer' and len(q['added_ids'])>2);tip=tuple(row['exit_nm']);start=tuple(row['heading_origin_nm']);h=tuple((tip[i]>start[i])-(tip[i]<start[i]) for i in (0,1));normal=(-h[1],h[0]);u=tuple(round(start[i]+(tip[i]-start[i])*.3)+normal[i]*100000 for i in (0,1));v=tuple(round(start[i]+(tip[i]-start[i])*.7)+normal[i]*100000 for i in (0,1));mid=tuple(round((start[i]+tip[i])/2)+normal[i]*250000 for i in (0,1))
results['finite']={'trace_overlap_rejected':not policy.accepts_segment(u,v,row['exit_layer'],row['net']),'annulus_overlap_rejected':hasattr(policy,'accepts_via') and not policy.accepts_via(mid,row['net'],600000)}
via_root=graph['find'](ids[col['terminal_id']]);via_indices=[i for i in range(len(graph['nodes'])) if graph['find'](i)==via_root];via_contacts=policy.contacts(graph,via_indices,(35000000,35000000));results['via_terminals']={'real_tip':(tuple(col['exit_nm']),col['exit_layer']) in via_contacts,'phantom_absent':(tuple(col['exit_nm']),p.F_Cu if col['exit_layer']==p.B_Cu else p.B_Cu) not in via_contacts}
for case in ('terminal','copper','pin','deadline','alias','interior','heading','via_id','via_nm'):

 trial=deepcopy(receipt);before=snapshot(b)
 if case=='terminal':trial['ports'][0]['terminal_id']='absent'
 if case=='copper':trial['tracks'][next(iter(trial['tracks']))]['width']+=1
 if case=='pin':trial['matrix_pins']['rows'].reverse()
 if case=='alias':
  q=next(q for q in trial['ports'] if len(q['pad_ids'])>1);uid=q['pad_ids'].pop();trial['pads'].pop(uid)
 if case=='interior':
  q=trial['ports'][0];track=trial['tracks'][q['terminal_id']];q['exit_nm']=[round((a+b)/2) for a,b in zip(track['start'],track['end'])]
 if case=='heading':
  q=trial['ports'][0];q['heading_origin_nm']=[2*a-b for a,b in zip(q['exit_nm'],q['heading_origin_nm'])]
 if case in ('via_id','via_nm'):
  q=next(q for q in trial['ports'] if q['kind']=='via')
  if case=='via_id':q['via_id']='absent'
  else:q['via_nm'][0]+=1000000

 try:verify_interfaces(b,cfg,trial,deadline=time.monotonic()-1 if case=='deadline' else time.monotonic()+10)
 except (ValueError,TimeoutError) as e:results[case]={'rejected':True,'unchanged':snapshot(b)==before,'error':str(e)}
 else:results[case]={'rejected':False}
# An ordered model cannot lose or empty its required handoff marker.
from smilepcb import keyboard_native as api
from smilepcb.keyboard_routing.interface_contract import MARKER
for case in ('missing','empty'):
 folder=out/case;folder.mkdir();path=folder/'keyboard.kicad_pcb';p.SaveBoard(str(path),b);required=deepcopy(cfg);footprints={f.GetReference():f.GetFPID().GetUniStringLibId() for f in b.GetFootprints()}
 for part in required['parts']:part['footprint']=footprints[part['ref']]
 required['routing_preparation']={'mode':'ordered-banks-v1'};(folder/'.smilepcb-keyboard.json').write_text(json.dumps(required))
 if case=='empty':(folder/MARKER).write_text('{}')
 raw=path.read_bytes();checks=[]
 for operation in ('controller_ports','matrix_field'):
  try:api.run({'operation':operation,'board':str(path),'generation_profile':'dsn-safe-v1','budget_s':1 if operation=='controller_ports' else .00001,'include_controller':True})
  except ValueError as e:checks.append('interface' in str(e).lower())
  else:checks.append(False)
 results[case]={'rejected_at_both_boundaries':all(checks),'source_unchanged':path.read_bytes()==raw}
# Native-clear foreign copper can still seal another interface's future exit.
q=next(q for q in ports if q['kind']=='same_layer');single=PortPolicy(b,[q]);tip=tuple(q['exit_nm']);origin=q['heading_origin_nm'];h=tuple((tip[i]>origin[i])-(tip[i]<origin[i]) for i in (0,1));normal=(-h[1],h[0]);cross=tuple(tip[i]+h[i]*450000 for i in (0,1));a=tuple(cross[i]-normal[i]*200000 for i in (0,1));z=tuple(cross[i]+normal[i]*200000 for i in (0,1));far=tuple(tip[i]+h[i]*1500000 for i in (0,1));other=p.B_Cu if q['exit_layer']==p.F_Cu else p.F_Cu
b.GetDesignSettings().m_TrackMinWidth=150000
oracle=LocalRouter(b,compact=True,track_width_nm=150000)
results['launch_reservation']={'native_clear':oracle.clear(p.SHAPE_SEGMENT(vec(a),vec(z),150000),q['exit_layer'],'probe'), 'foreign_blocked':not single.accepts_segment(a,z,q['exit_layer'],'probe'), 'own_allowed':single.accepts_segment(tip,tuple(tip[i]+h[i]*1000000 for i in (0,1)),q['exit_layer'],q['net']), 'other_side_allowed':single.accepts_segment(a,z,other,'probe'), 'via_blocked':not single.accepts_via(tuple(tip[i]+h[i]*750000 for i in (0,1)),'probe',500000), 'outside_window_allowed':single.accepts_segment(tuple(far[i]-normal[i]*200000 for i in (0,1)),tuple(far[i]+normal[i]*200000 for i in (0,1)),q['exit_layer'],'probe')}
# Group centroids are fractional even though native geometry is integer nm.
from smilepcb.keyboard_routing.native_graph import nearest_to_node,nearest_on_island
try:
 track=p.PCB_TRACK(b);track.SetStart(p.VECTOR2I(0,0));track.SetEnd(p.VECTOR2I(100,0))
 shape=p.SHAPE_POLY_SET();shape.NewOutline()
 for x,y in ((0,0),(100,0),(100,100),(0,100)):shape.Append(x,y)
 results['fractional_contacts']={'track':nearest_to_node({'zone':False,'item':track},(51.4,40.4))==(51,0),'inside_zone':nearest_on_island(shape,(50.2,50.8))==(50,51),'outside_zone':nearest_on_island(shape,(101.2,-.8))==(100,0)}
except (TypeError,ValueError) as error:results['fractional_contacts']={'native_integer_conversion':False,'error':str(error)}
(out/'report.json').write_text(json.dumps(results,indent=2))
