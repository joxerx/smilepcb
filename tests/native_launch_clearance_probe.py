"""Higher owner/foreign net clearance must also protect virtual launch space."""
import json,sys
from pathlib import Path
from smilepcb.native_worker import p
from smilepcb.keyboard_routing.port_policy import PortPolicy
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);report={}
for high in ('owner','foreign'):
 b=p.BOARD();rules=b.GetDesignSettings();rules.m_MinClearance=200000;rules.m_TrackMinWidth=150000
 for n in ('owner','foreign'):b.Add(p.NETINFO_ITEM(b,n))
 for a,z in (((0,0),(30000000,0)),((30000000,0),(30000000,20000000)),((30000000,20000000),(0,20000000)),((0,20000000),(0,0))):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(vec(a));e.SetEnd(vec(z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
 pads=[]
 for ref,net,xy in (('U_MCU','owner',(5000000,5000000)),('owner_pad','owner',(5000000,15000000)),('foreign_pad','foreign',(20000000,15000000))):
  f=p.FOOTPRINT(b);f.SetReference(ref);b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetPosition(vec(xy));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(vec((300000,300000)));ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet(net));f.Add(d);pads.append(d)
 pads[1 if high=='owner' else 2].SetLocalClearance(600000)
 t=p.PCB_TRACK(b);t.SetStart(vec((5000000,5000000)));t.SetEnd(vec((10000000,5000000)));t.SetWidth(150000);t.SetLayer(p.F_Cu);t.SetNet(b.FindNet('owner'));b.Add(t)
 q={'net':'owner','pad_ids':[pads[0].m_Uuid.AsString()],'added_ids':[t.m_Uuid.AsString()],'terminal_id':t.m_Uuid.AsString(),'kind':'same_layer','exit_nm':(10000000,5000000),'heading_origin_nm':(5000000,5000000),'exit_layer':p.F_Cu};policy=PortPolicy(b,[q]);a,z=(10860000,4000000),(10860000,6000000);shape=p.SHAPE_SEGMENT(vec(a),vec(z),150000);world=LocalRouter(b,compact=True,track_width_nm=150000)
 row={'native_clear':world.clear(shape,p.F_Cu,'foreign'),'launch_protected':not policy.accepts_segment(a,z,p.F_Cu,'foreign',150000)}
 world.port_policy=policy;row['native_planner_respects_launch']=not world.clear(shape,p.F_Cu,'foreign')
 del world.port_policy
 from smilepcb.keyboard_routing.interface_launches import reserve_launches
 with reserve_launches([q]):
  scoped=LocalRouter(b,compact=True,track_width_nm=150000)
  row['scoped_short_path_protected']=not scoped.clear(shape,p.F_Cu,'foreign')
  row['other_face_available']=scoped.clear(shape,p.B_Cu,'foreign')
  row['owner_departure_available']=scoped.clear(p.SHAPE_SEGMENT(vec((10000000,5000000)),vec((10125000,5000000)),150000),p.F_Cu,'owner')
  try:
   with reserve_launches([]):
    row['nested_scope_independent']=LocalRouter(b,compact=True,track_width_nm=150000).clear(shape,p.F_Cu,'foreign')
    raise RuntimeError('test scope restoration')
  except RuntimeError:pass
  row['outer_scope_restored']=not LocalRouter(b,compact=True,track_width_nm=150000).clear(shape,p.F_Cu,'foreign')
 row['reservation_does_not_leak']=LocalRouter(b,compact=True,track_width_nm=150000).clear(shape,p.F_Cu,'foreign')
 row['router_retains_captured_scope']=not scoped.clear(shape,p.F_Cu,'foreign')
 if high=='owner':
  foreign=p.PCB_TRACK(b);foreign.SetStart(vec(a));foreign.SetEnd(vec(z));foreign.SetWidth(150000);foreign.SetLayer(p.F_Cu);foreign.SetNet(b.FindNet('foreign'));b.Add(foreign);world=LocalRouter(b,compact=True,track_width_nm=150000);row['first_step_blocked_without_reservation']=not world.clear(p.SHAPE_SEGMENT(vec((10000000,5000000)),vec((10125000,5000000)),150000),p.F_Cu,'owner')
 report[high]=row
(out/'report.json').write_text(json.dumps(report,indent=2))
