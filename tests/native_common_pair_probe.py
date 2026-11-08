"""Source tangents, a shared common rail and atomic rejection on real copper."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.common_pair import try_common_pair
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.role_quality import whole_board_coverage
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
v=lambda x,y:p.VECTOR2I(round(x*1e6),round(y*1e6))
def make(blocked=False):
 b=p.BOARD();b.GetDesignSettings().m_TrackMinWidth=150000;b.GetDesignSettings().m_MinClearance=150000
 for n in ('minus','plus','wall'):b.Add(p.NETINFO_ITEM(b,n))
 for a,z in (((0,0),(30,0)),((30,0),(30,16)),((30,16),(0,16)),((0,16),(0,0))):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(v(*a));e.SetEnd(v(*z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
 for ref,origin,contacts in [('source',(8,3),[('minus',(8,5)),('plus',(8.53316,5))]),('target',(28,6.5),[('minus',(26,7)),('plus',(26,6))])]:
  f=p.FOOTPRINT(b);f.SetReference(ref);f.SetPosition(v(*origin));b.Add(f)
  for i,(n,pos) in enumerate(contacts):
   d=p.PAD(f);d.SetNumber(str(i+1));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);d.SetSize(v(.25,.25));d.SetPosition(v(*pos));d.SetNet(b.FindNet(n));f.Add(d)
 for n,a,z in [('minus',(8,5),(7,6)),('plus',(8.53316,5),(7.53316,6))]+([('wall',(15,0),(15,16))] if blocked else []):
  t=p.PCB_TRACK(b);t.SetStart(v(*a));t.SetEnd(v(*z));t.SetWidth(200000 if n!='wall' else 500000);t.SetLayer(p.F_Cu);t.SetNet(b.FindNet(n));b.Add(t)
 return b

def join(b,deadline=None):
 with generation_profile('dsn-process-v2'):
  return try_common_pair(b,('minus','plus'),source_ref='source',target_ref='target',layer=p.F_Cu,deadline=deadline or time.monotonic()+12,max_length_nm=None)
report={};b=make();old=snapshot(b);proof=join(b);new=snapshot(b)
report['complete_pair']=proof is not None and len(copper_graph(b,{'minus','plus'})['groups'])==2
report['protected_exact']=new['footprints']==old['footprints'] and all(new['tracks'].get(u)==t for u,t in old['tracks'].items())
report['outgoing_tangent']=bool(proof) and all(q['points_nm'][1][0]<q['points_nm'][0][0] and q['points_nm'][1][1]>q['points_nm'][0][1] for q in proof.values())
def edges(points):return {tuple(sorted((tuple(a),tuple(z)))) for a,z in zip(points,points[1:])}
report['receipt_geometry_exact']=bool(proof) and all(edges(q['points_nm'])=={tuple(sorted((tuple(new['tracks'][u]['start']),tuple(new['tracks'][u]['end'])))) for u in q['added_ids']} for q in proof.values())
report['inner_tuning_report']=bool(proof) and any(q.get('balance',{}).get('changed') for q in proof.values())
import smilepcb.keyboard_routing.native_graph as ng
original_graph=ng.copper_graph
b_timeout=make();timeout_before=snapshot(b_timeout)
def exhausted(*a,**kw):raise TimeoutError('deliberate pre-mutation discovery timeout')
ng.copper_graph=exhausted
try:
 try:timeout_result=join(b_timeout);report['discovery_timeout_atomic']=timeout_result is None and snapshot(b_timeout)==timeout_before
 except TimeoutError:report['discovery_timeout_atomic']=False
finally:ng.copper_graph=original_graph
coverage=whole_board_coverage(new['tracks'],{'pair':{'minus','plus'}},(0,0,30000000,16000000),preferred_edge_gap_nm=153554)
report['long_common_run']=coverage['pair']>5e6 and bool(proof) and any(a[1]==z[1] and abs(a[0]-z[0])>12e6 for a,z in zip(next(iter(proof.values()))['common_spine_nm'],next(iter(proof.values()))['common_spine_nm'][1:]))
repeat=join(b);report['idempotent']=snapshot(b)==new and repeat is not None and all(not q['added_ids'] for q in repeat.values())
b=make(True);old=snapshot(b);report['blocked_atomic']=join(b) is None and snapshot(b)==old
b=make();old=snapshot(b);report['expired_atomic']=join(b,time.monotonic()-1) is None and snapshot(b)==old
b=make();mapping={'minus':'USB_DP','plus':'USB_DM'}
for name in mapping.values():b.Add(p.NETINFO_ITEM(b,name))
for f in b.GetFootprints():
 f.SetReference({'source':'U_MCU','target':'J_UDB'}[f.GetReference()])
 for pad in f.Pads():pad.SetNet(b.FindNet(mapping[pad.GetNetname()]))
for t in b.GetTracks():t.SetNet(b.FindNet(mapping[t.GetNetname()]))
from smilepcb.keyboard_routing.usb import route_controller_usb
from smilepcb.routing_quality import using_quality_profile
with generation_profile('dsn-process-v2'),using_quality_profile('turns'):
 result=route_controller_usb(b,deadline=time.monotonic()+12,max_length_nm=None)
report['main_usb_uses_common_corridor']=result['accepted'] and all(q['method']=='joint common rail' for q in result['usb'].values())
from smilepcb.keyboard_routing.local_router import LocalRouter
b=make();t=p.PCB_TRACK(b);t.SetStart(v(2,10));t.SetEnd(v(28,10));t.SetWidth(200000);t.SetLayer(p.F_Cu);t.SetNet(b.FindNet('wall'));b.Add(t)
with generation_profile('dsn-process-v2'),using_quality_profile('turns'):
 router=LocalRouter(b,deadline=time.monotonic()+5,compact=True)
 leg=router.leg((2000000,12000000),(28000000,12000000),p.F_Cu,'plus')
report['ordinary_router_follows_foreign_bundle']=leg is not None and any(a[1]==z[1] and 10200000<=a[1]<=11200000 and abs(a[0]-z[0])>15e6 for a,z in zip(leg,leg[1:]))
(out/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
