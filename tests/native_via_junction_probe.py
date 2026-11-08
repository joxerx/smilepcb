"""Replacing all via arms must remove, not extend, old copper."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.completion import pad_partitions
from smilepcb.routing_quality import copper_metrics
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
try:from smilepcb.keyboard_routing.via_junction_polish import polish_via_junctions,via_stars,replace_junction
except ImportError:
 (out/'result.json').write_text(json.dumps({'missing_api':True}));raise SystemExit(0)
M=1000000

def fixture(third=False,ground=False):
 b=p.BOARD();b.SetCopperLayerCount(2);rules=b.GetDesignSettings();rules.m_MinClearance=150000;rules.m_TrackMinWidth=150000;rules.m_HoleClearance=250000
 for net in ['bus','earth','neighbor']:b.Add(p.NETINFO_ITEM(b,net))
 for a,z in zip([(0,0),(30,0),(30,22),(0,22)],[(30,0),(30,22),(0,22),(0,0)]):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);e.SetStart(p.VECTOR2I(*(int(x*M) for x in a)));e.SetEnd(p.VECTOR2I(*(int(x*M) for x in z)));b.Add(e)
 def pad(ref,net,face,q):
  fp=p.FOOTPRINT(b);fp.SetReference(ref);b.Add(fp);d=p.PAD(fp);d.SetNumber('1');d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(800000,800000));d.SetPosition(p.VECTOR2I(*(int(x*M) for x in q)));ls=p.LSET();ls.AddLayer(face);d.SetLayerSet(ls);d.SetNet(b.FindNet(net));fp.Add(d)
 arms=[(p.F_Cu,[(2,6),(4,6),(8,10),(8,12),(10,14),(12,14),(14,12)]),(p.B_Cu,[(22,12),(20,12),(18,10),(16,10),(14,12)])]
 if third:arms.append((p.B_Cu,[(10,18),(12,16),(12,14),(14,12)]))
 for i,(face,path) in enumerate(arms):
  pad('P'+str(i),'bus',face,path[0])
  for a,z in zip(path,path[1:]):
   t=p.PCB_TRACK(b);t.SetLayer(face);t.SetWidth(200000);t.SetNet(b.FindNet('bus'));t.SetStart(p.VECTOR2I(*(int(x*M) for x in a)));t.SetEnd(p.VECTOR2I(*(int(x*M) for x in z)));b.Add(t)
 via=p.PCB_VIA(b);via.SetNet(b.FindNet('bus'));via.SetPosition(p.VECTOR2I(14*M,12*M));via.SetWidth(700000);via.SetDrill(300000);via.SetLayerPair(p.F_Cu,p.B_Cu);b.Add(via)
 planes=set()
 if ground:
  pad('G1','earth',p.F_Cu,(10,3));pad('G2','earth',p.F_Cu,(10,8))
  zone=p.ZONE(b);zone.SetNet(b.FindNet('earth'));zone.SetLayer(p.F_Cu);zone.SetMinThickness(150000);zone.SetLocalClearance(200000);zone.SetPadConnection(p.ZONE_CONNECTION_FULL)
  poly=zone.Outline();poly.NewOutline()
  for q in [(8,2),(12,2),(12,4),(10.3,4),(10.3,7),(12,7),(12,9),(8,9),(8,7),(9.7,7),(9.7,4),(8,4)]:poly.Append(*(round(x*M) for x in q))
  b.Add(zone);assert p.ZONE_FILLER(b).Fill(b.Zones());planes.add(zone.m_Uuid.AsString())
 return b,via,planes

parts=lambda b:{frozenset(q) for q in pad_partitions(b,time.monotonic()+10)}
result={}
for case in ['movable','third-arm','locked-via','locked-arm','expired']:
 b,v,z=fixture(case=='third-arm');uid=v.m_Uuid.AsString()
 if case=='locked-via':v.SetLocked(True)
 if case=='locked-arm':next(t for t in b.GetTracks() if t.GetClass()=='PCB_TRACK' and t.GetLayer()==p.B_Cu).SetLocked(True)
 before=snapshot(b);contacts=parts(b)
 report=polish_via_junctions(b,eligible_nets={'bus'},protected_ids=(),deadline=time.monotonic()-1 if case=='expired' else time.monotonic()+12,deferred_zone_ids=z,group_strength=0)
 after=snapshot(b);p.SaveBoard(str(out/(case+'.kicad_pcb')),b);saved=p.LoadBoard(str(out/(case+'.kicad_pcb')))
 result[case]={'report':report,'before_metrics':copper_metrics(before['tracks']),'after_metrics':copper_metrics(after['tracks']),'unchanged':before==after,'contacts_exact':contacts==parts(b),'saved_exact':snapshot(saved)==after,'footprints_exact':before['footprints']==after['footprints'],'via_identity_exact':uid in after['tracks'],'via_width_nm':after['tracks'].get(uid,{}).get('width'),'via_drill_nm':after['tracks'].get(uid,{}).get('drill')}
# The geometrically clear straight line severs a real narrow GND neck.
b,v,z=fixture(ground=True);before=snapshot(b);contacts=parts(b);star=via_stars(b,eligible_nets={'bus'},protected_ids=(),deadline=time.monotonic()+10,deferred_zone_ids=z)[0]
new=(14*M,6*M);paths=[[(2*M,6*M),new] if arm['layer']==p.F_Cu else [(22*M,12*M),(20*M,12*M),new] for arm in star['arms']]
r=replace_junction(b,star,new,paths,protected_ids=(),deadline=time.monotonic()+10,deferred_zone_ids=z)
result['ground-rollback']={'report':r,'unchanged':snapshot(b)==before,'contacts_exact':parts(b)==contacts}
# Locks are re-read when applying a previously discovered star.
b,v,z=fixture();star=via_stars(b,eligible_nets={'bus'},protected_ids=(),deadline=time.monotonic()+10)[0];uid=star['arms'][0]['ids'][0];next(t for t in b.GetTracks() if t.m_Uuid.AsString()==uid).SetLocked(True);before=snapshot(b)
r=replace_junction(b,star,(14*M,6*M),[[(2*M,6*M),(14*M,6*M)] if a['layer']==p.F_Cu else [(22*M,12*M),(20*M,12*M),(14*M,6*M)] for a in star['arms']],protected_ids=(),deadline=time.monotonic()+10)
result['late-lock']={'report':r,'unchanged':snapshot(b)==before}
# A real partial mutation followed by an exception must restore exact source.
from smilepcb.keyboard_routing.local_router import LocalRouter
b,v,z=fixture();star=via_stars(b,eligible_nets={'bus'},protected_ids=(),deadline=time.monotonic()+10)[0];before=snapshot(b);contacts=parts(b);original_add=LocalRouter.add_path
new=(14*M,6*M);paths=[[(2*M,6*M),new] if a['layer']==p.F_Cu else [(22*M,12*M),(20*M,12*M),new] for a in star['arms']]
def partial_failure(self,path,layer,net):
 original_add(self,path[:2],layer,net)
 raise RuntimeError('Injected failure after one real native segment')
LocalRouter.add_path=partial_failure
try:
 try:replace_junction(b,star,new,paths,protected_ids=(),deadline=time.monotonic()+10)
 except Exception as error:fault_type=type(error).__name__
finally:LocalRouter.add_path=original_add
result['partial-emission']={'unchanged':snapshot(b)==before,'contacts_exact':parts(b)==contacts,'fault_type':fault_type}
# A failure between board.Add and index bookkeeping must also be atomic.
b,v,z=fixture();star=via_stars(b,eligible_nets={'bus'},protected_ids=(),deadline=time.monotonic()+10)[0];before=snapshot(b);contacts=parts(b);original_index=LocalRouter.index;fault_type=None
new=(14*M,6*M);paths=[[(2*M,6*M),new] if a['layer']==p.F_Cu else [(22*M,12*M),(20*M,12*M),new] for a in star['arms']]
def index_failure(self,item):
 if item.GetClass()=='PCB_TRACK' and item.m_Uuid.AsString() not in before['tracks']:raise RuntimeError('Injected index failure after board.Add')
 return original_index(self,item)
LocalRouter.index=index_failure
try:
 try:replace_junction(b,star,new,paths,protected_ids=(),deadline=time.monotonic()+10)
 except Exception as error:fault_type=type(error).__name__
finally:LocalRouter.index=original_index
result['pre-bookkeeping-emission']={'unchanged':snapshot(b)==before,'contacts_exact':parts(b)==contacts,'fault_type':fault_type}
# An implicit old mid-segment branch is not safe to delete as a simple arm.
b,v,z=fixture();t=p.PCB_TRACK(b);t.SetNet(b.FindNet('bus'));t.SetLayer(p.F_Cu);t.SetWidth(200000);t.SetStart(p.VECTOR2I(6*M,8*M));t.SetEnd(p.VECTOR2I(6*M,12*M));b.Add(t)
result['unmodeled-branch']={'star_count':len(via_stars(b,eligible_nets={'bus'},protected_ids=(),deadline=time.monotonic()+10))}
# Same-net pad grazing can create an invisible-to-DRC extra loop.
b,v,z=fixture();fp=p.FOOTPRINT(b);fp.SetReference('side');b.Add(fp);d=p.PAD(fp);d.SetNumber('1');d.SetNet(b.FindNet('bus'));d.SetSize(p.VECTOR2I(800000,800000));d.SetPosition(p.VECTOR2I(10*M,6450000));d.SetShape(p.PAD_SHAPE_RECT);d.SetAttribute(p.PAD_ATTRIB_SMD);ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);fp.Add(d)
v2=p.PCB_VIA(b);v2.SetNet(b.FindNet('bus'));v2.SetPosition(p.VECTOR2I(10*M,7800000));v2.SetWidth(700000);v2.SetDrill(300000);v2.SetLayerPair(p.F_Cu,p.B_Cu);b.Add(v2)
for layer,path in [(p.F_Cu,[(10*M,6450000),(10*M,7800000)]),(p.B_Cu,[(10*M,7800000),(10*M,18*M),(22*M,18*M),(22*M,12*M)])]:
 for a,c in zip(path,path[1:]):
  t=p.PCB_TRACK(b);t.SetNet(b.FindNet('bus'));t.SetLayer(layer);t.SetWidth(200000);t.SetStart(p.VECTOR2I(*a));t.SetEnd(p.VECTOR2I(*c));b.Add(t)
star=next(q for q in via_stars(b,eligible_nets={'bus'},protected_ids=(),deadline=time.monotonic()+10) if q['via_id']==v.m_Uuid.AsString());before=snapshot(b)
r=replace_junction(b,star,(14*M,6*M),[[(2*M,6*M),(14*M,6*M)] if a['layer']==p.F_Cu else [(22*M,12*M),(20*M,12*M),(14*M,6*M)] for a in star['arms']],protected_ids=(),deadline=time.monotonic()+10)
result['pad-graze']={'report':r,'unchanged':snapshot(b)==before}
# Both the search and a direct star application must honour area quality.
b,v,z=fixture();before=snapshot(b)
try:r=polish_via_junctions(b,eligible_nets={'bus'},protected_ids=(),deadline=time.monotonic()+12,quality_gate=lambda a,b:False)
except TypeError as error:r={'fault':str(error)}
result['quality-veto-search']={'report':r,'unchanged':snapshot(b)==before}
b,v,z=fixture();star=via_stars(b,eligible_nets={'bus'},protected_ids=(),deadline=time.monotonic()+10)[0];before=snapshot(b)
paths=[[(2*M,6*M),(14*M,6*M)] if a['layer']==p.F_Cu else [(22*M,12*M),(20*M,12*M),(14*M,6*M)] for a in star['arms']]
try:r=replace_junction(b,star,(14*M,6*M),paths,protected_ids=(),deadline=time.monotonic()+12,quality_gate=lambda a,b:False)
except TypeError as error:r={'fault':str(error)}
result['quality-veto-direct']={'report':r,'unchanged':snapshot(b)==before}
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
