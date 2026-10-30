"""Native reuse of actual local plated access, including repeated passes."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.power import seed_plane_access

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
def v(x,y):return p.VECTOR2I(p.FromMM(x),p.FromMM(y))
def base():
 b=p.BOARD();b.GetDesignSettings().m_TrackMinWidth=150000;b.GetDesignSettings().m_MinClearance=150000
 b.Add(p.NETINFO_ITEM(b,'return'))
 for a,z in (((0,0),(30,0)),((30,0),(30,30)),((30,30),(0,30)),((0,30),(0,0))):
  edge=p.PCB_SHAPE(b);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(v(*a));edge.SetEnd(v(*z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);b.Add(edge)
 return b
def pad(b,ref,x=10,y=10):
 f=p.FOOTPRINT(b);f.SetReference(ref);b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT)
 layers=p.LSET();layers.AddLayer(p.F_Cu);d.SetLayerSet(layers);d.SetSize(v(1,1));d.SetPosition(v(x,y));d.SetNet(b.FindNet('return'));f.Add(d);return d
def via(b,x,y):
 t=p.PCB_VIA(b);t.SetPosition(v(x,y));t.SetWidth(600000);t.SetDrill(300000);t.SetLayerPair(p.F_Cu,p.B_Cu);t.SetNet(b.FindNet('return'));b.Add(t);return t.m_Uuid.AsString()
def track(b,a,z):
 t=p.PCB_TRACK(b);t.SetStart(v(*a));t.SetEnd(v(*z));t.SetWidth(150000);t.SetLayer(p.F_Cu);t.SetNet(b.FindNet('return'));b.Add(t)
def seed(b,pads,width=150000):return seed_plane_access(b,pads,deadline=time.monotonic()+15,track_width_nm=width,max_distance_nm=2000000)
report={}
b=base();d=pad(b,'P1');first=seed(b,[d]);before=snapshot(b);second=seed(b,[d],200000);after=snapshot(b)
report['repeat']={'first_connected':first[0]['connected'],'second_connected':second[0]['connected'],
 'same_via':second[0].get('via_id')==first[0]['via_id'],'no_new_ids':second[0]['ids']==[],
 'all_copper_unchanged':after==before}
b=base();d=pad(b,'P1');unrelated=via(b,11.5,10);row=seed(b,[d])[0]
report['disconnected_same_net']={'connected':row['connected'],'created_new_access':bool(row['ids']) and row['via_id']!=unrelated}
b=base();a=pad(b,'P1');z=pad(b,'P2',12,10);track(b,(10,10),(12,10));rows=seed(b,[a,z])
report['shared_group']={'both_connected':all(r['connected'] for r in rows),'one_via':len({r.get('via_id') for r in rows})==1,
 'second_has_no_additions':rows[1]['ids']==[]}
b=base();d=pad(b,'P1');remote=via(b,14,10);track(b,(10,10),(14,10));row=seed(b,[d])[0]
report['distant_access']={'connected':row['connected'],'new_local_access':bool(row['ids']) and row['via_id']!=remote}
b=base();d=pad(b,'P1');first=seed(b,[d])[0]
second=seed_plane_access(b,[d],deadline=time.monotonic()+15,track_width_nm=200000,
                         max_distance_nm=2000000,via_to_smd_clearance_nm=350000)[0]
report['stricter_land_margin']={'connected':second['connected'],
 'new_access':bool(second['ids']) and first['via_id']!=second['via_id']}
b=base();d=pad(b,'P1');unrelated=via(b,11.5,10)
zone=p.ZONE(b);zone.SetLayer(p.F_Cu);zone.SetNet(b.FindNet('return'))
outline=zone.Outline();outline.NewOutline()
for x,y in ((7,7),(14,7),(14,14),(7,14)):outline.Append(p.FromMM(x),p.FromMM(y))
b.Add(zone)
from smilepcb.keyboard_routing.native_graph import copper_graph
try:copper_graph(b,{'return'});rejected=False
except ValueError:rejected=True
explicit=copper_graph(b,{'return'},include_zones=False)
try:seed(b,[d]);route_rejected=False
except ValueError:route_rejected=True
report['unfilled_plane']={'default_graph_rejects':rejected,'explicit_groups_separate':len(explicit['groups'])==2,
 'routing_still_rejects_unfilled_planes':route_rejected}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
