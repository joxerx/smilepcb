import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.completion import pad_partitions
from smilepcb.keyboard_routing.polish_geometry import valid_shape
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
try:from smilepcb.keyboard_routing.bundle_polish import refine_bundles
except ImportError:
 (out/'result.json').write_text(json.dumps({'missing_api':True}));raise SystemExit(0)
M=1000000

def board(ground_cut=False):
 b=p.BOARD();b.SetCopperLayerCount(2);r=b.GetDesignSettings();r.m_MinClearance=150000;r.m_TrackMinWidth=150000
 for n in ('first','second','earth'):b.Add(p.NETINFO_ITEM(b,n))
 for a,z in zip([(0,0),(20*M,0),(20*M,15*M),(0,15*M)],[(20*M,0),(20*M,15*M),(0,15*M),(0,0)]):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetLayer(p.Edge_Cuts);e.SetStart(p.VECTOR2I(*a));e.SetEnd(p.VECTOR2I(*z));e.SetWidth(50000);b.Add(e)
 for i,(net,xy) in enumerate([('first',(2*M,5*M)),('first',(18*M,5*M)),('second',(2*M,6*M)),('second',(18*M,6*M)),('earth',((10 if ground_cut else 2)*M,(3 if ground_cut else 2)*M)),('earth',((10 if ground_cut else 18)*M,12*M))]):
  f=p.FOOTPRINT(b);f.SetReference('p'+str(i));b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetPosition(p.VECTOR2I(*xy));d.SetSize(p.VECTOR2I(500000,500000));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet(net));f.Add(d)
 path=[(2*M,5*M),(4*M,5*M),(6*M,3*M),(8*M,3*M),(12*M,7*M),(14*M,7*M),(16*M,5*M),(18*M,5*M)]
 if ground_cut:path=[(2*M,5*M),(4*M,3*M),(4*M,2*M),(5*M,M),(15*M,M),(16*M,2*M),(16*M,3*M),(18*M,5*M)]
 for name,dy in [('first',0),('second',M)]:
  for a,z in zip(path,path[1:]):
   t=p.PCB_TRACK(b);t.SetNet(b.FindNet(name));t.SetLayer(p.F_Cu);t.SetWidth(150000);t.SetStart(p.VECTOR2I(a[0],a[1]+dy));t.SetEnd(p.VECTOR2I(z[0],z[1]+dy));b.Add(t)
 z=p.ZONE(b);z.SetLayer(p.F_Cu);z.SetNet(b.FindNet('earth'));z.SetMinThickness(150000);z.SetLocalClearance(200000);z.SetPadConnection(p.ZONE_CONNECTION_FULL);poly=z.Outline();poly.NewOutline()
 outline=[(M,M),(19*M,M),(19*M,14*M),(M,14*M)]
 if ground_cut:outline=[(8*M,2500000),(12*M,2500000),(12*M,4*M),(10200000,4*M),(10200000,8*M),(12*M,8*M),(12*M,13*M),(8*M,13*M),(8*M,8*M),(9800000,8*M),(9800000,4*M),(8*M,4*M)]
 for point in outline:poly.Append(*point)
 b.Add(z);assert p.ZONE_FILLER(b).Fill(b.Zones());return b,z.m_Uuid.AsString()
result={}
for case in ('joint','fixed-fill','expired','locked','ground-cut'):
 b,z=board(case=='ground-cut')
 if case=='locked':
  for t in b.GetTracks():
   if t.GetNetname()=='first':t.SetLocked(True)
 before=snapshot(b);parts={frozenset(q) for q in pad_partitions(b,time.monotonic()+5)}
 report=refine_bundles(b,eligible_nets={'first','second'},protected_ids=(),deferred_zone_ids=() if case=='fixed-fill' else {z},deadline=time.monotonic()-1 if case=='expired' else time.monotonic()+15)
 after=snapshot(b);result[case]={'report':report,'unchanged':before==after,'footprints_exact':before['footprints']==after['footprints'],'contacts_exact':parts=={frozenset(q) for q in pad_partitions(b,time.monotonic()+5)},'locked_exact':all(after['tracks'].get(u)==v for u,v in before['tracks'].items() if v['locked']),'paths_valid':all(valid_shape(path) for q in report.get('rewrites',[]) for path in q['new'])}
 p.SaveBoard(str(out/(case+'.kicad_pcb')),b)
try:
 from smilepcb.keyboard_routing.bundle_polish import retained_contacts_clear
except ImportError:result['retained-loop']={'missing_api':True}
else:
 from smilepcb.keyboard_routing.local_router import LocalRouter
 b,z=board();ignored={t.m_Uuid.AsString() for t in b.GetTracks()}
 branch=[(2*M,5*M),(2*M,9*M),(10*M,9*M),(10*M,5*M),(12*M,3*M)]
 for a,c in zip(branch,branch[1:]):
  t=p.PCB_TRACK(b);t.SetNet(b.FindNet('first'));t.SetLayer(p.F_Cu);t.SetWidth(150000);t.SetStart(p.VECTOR2I(*a));t.SetEnd(p.VECTOR2I(*c));b.Add(t)
 oracle=LocalRouter(b,ignored_copper=ignored,compact=True,track_width_nm=150000,deferred_zone_ids={z});before=snapshot(b)
 straight=[(2*M,5*M),(18*M,5*M)];detour=[(2*M,5*M),(5*M,2*M),(15*M,2*M),(18*M,5*M)]
 result['retained-loop']={'new_contact_rejected':not retained_contacts_clear(b,straight,p.F_Cu,'first',150000,ignored_ids=ignored,oracle=oracle),'endpoint_contact_allowed':retained_contacts_clear(b,detour,p.F_Cu,'first',150000,ignored_ids=ignored,oracle=oracle),'unchanged':snapshot(b)==before}

b,z=board();ignored={t.m_Uuid.AsString() for t in b.GetTracks()}
v=p.PCB_VIA(b);v.SetNet(b.FindNet('first'));v.SetPosition(p.VECTOR2I(2*M,5*M));v.SetWidth(500000);v.SetDrill(200000);v.SetLayerPair(p.F_Cu,p.B_Cu);b.Add(v)
from smilepcb.keyboard_routing.local_router import LocalRouter
oracle=LocalRouter(b,ignored_copper=ignored,compact=True,track_width_nm=150000,deferred_zone_ids={z})
result['via-endpoint']={'allowed':retained_contacts_clear(b,[(2*M,5*M),(18*M,5*M)],p.F_Cu,'first',150000,ignored_ids=ignored,oracle=oracle),'actual_diameter_nm':v.GetWidth(p.F_Cu)}

# A good earlier rewrite must survive a later ground-cut rejection.
from smilepcb.keyboard_routing.matrix_polish import polish_matrix
b,z=board(True);b.Add(p.NETINFO_ITEM(b,'third'))
for i,point in enumerate([(2*M,10*M),(18*M,10*M)]):
 f=p.FOOTPRINT(b);f.SetReference('back'+str(i));b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetPosition(p.VECTOR2I(*point));d.SetSize(p.VECTOR2I(500000,500000));d.SetShape(p.PAD_SHAPE_RECT);d.SetAttribute(p.PAD_ATTRIB_SMD);ls=p.LSET();ls.AddLayer(p.B_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet('third'));f.Add(d)
path=[(2*M,10*M),(3*M,10*M),(4*M,11*M),(5*M,11*M),(6*M,10*M),(7*M,10*M),(8*M,11*M),(9*M,11*M),(10*M,10*M),(11*M,10*M),(12*M,11*M),(13*M,11*M),(14*M,10*M),(18*M,10*M)]
for a,c in zip(path,path[1:]):
 t=p.PCB_TRACK(b);t.SetNet(b.FindNet('third'));t.SetLayer(p.B_Cu);t.SetWidth(150000);t.SetStart(p.VECTOR2I(*a));t.SetEnd(p.VECTOR2I(*c));b.Add(t)
assert p.ZONE_FILLER(b).Fill(b.Zones());before=snapshot(b);parts={frozenset(q) for q in pad_partitions(b,time.monotonic()+5)}
r=polish_matrix(b,protected_ids=(),eligible_nets={'first','second','third'},deferred_zone_ids={z},deadline=time.monotonic()+15)
result['partial-single']={'report':r,'contacts_exact':parts=={frozenset(q) for q in pad_partitions(b,time.monotonic()+5)},'improved':snapshot(b)!=before}
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
