import sys,json,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.local_router import LocalRouter
try:from smilepcb.keyboard_routing.layer_policy import migrate_layers
except ImportError:migrate_layers=None
M=1000000
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
def fixture(vias=False,block=False):
 b=p.BOARD();b.SetCopperLayerCount(2);r=b.GetDesignSettings();r.m_MinClearance=150000;r.m_TrackMinWidth=150000
 for net in ('purple','foreign'):b.Add(p.NETINFO_ITEM(b,net))
 for a,z in zip([(0,0),(30,0),(30,20),(0,20)],[(30,0),(30,20),(0,20),(0,0)]):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);e.SetStart(p.VECTOR2I(*(v*M for v in a)));e.SetEnd(p.VECTOR2I(*(v*M for v in z)));b.Add(e)
 for i,x in enumerate((3,27)):
  fp=p.FOOTPRINT(b);fp.SetReference('P'+str(i));b.Add(fp);d=p.PAD(fp);d.SetNumber('1');d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(800000,800000));d.SetPosition(p.VECTOR2I(x*M,10*M));ls=p.LSET();ls.AddLayer(p.B_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet('purple'));fp.Add(d)
 points=[3,4,26,27] if vias else [3,27]
 for a,z in zip(points,points[1:]):
  t=p.PCB_TRACK(b);t.SetNet(b.FindNet('purple'));t.SetLayer(p.B_Cu);t.SetWidth(150000);t.SetStart(p.VECTOR2I(a*M,10*M));t.SetEnd(p.VECTOR2I(z*M,10*M));b.Add(t)
 if vias:
  for x in (4,26):
   v=p.PCB_VIA(b);v.SetNet(b.FindNet('purple'));v.SetPosition(p.VECTOR2I(x*M,10*M));v.SetWidth(500000);v.SetDrill(200000);v.SetLayerPair(p.F_Cu,p.B_Cu);b.Add(v)
 if block:
  t=p.PCB_TRACK(b);t.SetNet(b.FindNet('foreign'));t.SetLayer(p.F_Cu);t.SetWidth(300000);t.SetStart(p.VECTOR2I(M,10*M));t.SetEnd(p.VECTOR2I(29*M,10*M));t.SetLocked(True);b.Add(t)
 return b
result={}
for case in ('smd','existing-vias','blocked','expired','partial-fault','partial-expiry'):
 b=fixture(case=='existing-vias',case=='blocked');before=snapshot(b);original=LocalRouter.index
 if case in ('partial-fault','partial-expiry'):
  def fail(self,item):
   if item.GetClass()=='PCB_TRACK' and item.m_Uuid.AsString() not in before['tracks']:raise (TimeoutError('Injected per-chain expiry after board.Add') if case=='partial-expiry' else RuntimeError('Injected after board.Add'))
   return original(self,item)
  LocalRouter.index=fail
 try:
  if migrate_layers is None:r={'missing_api':True}
  else:r=migrate_layers(b,assignments={'purple':p.F_Cu},protected_ids=(),deadline=time.monotonic()-1 if case=='expired' else time.monotonic()+20,sweeps=1)
 except Exception as e:r={'fault':type(e).__name__+': '+str(e)}
 finally:LocalRouter.index=original
 after=snapshot(b);length={l:sum(t.GetLength() for t in b.GetTracks() if t.GetClass()=='PCB_TRACK' and t.GetNetname()=='purple' and t.GetLayer()==l) for l in (p.F_Cu,p.B_Cu)};result[case]={'report':r,'unchanged':before==after,'footprints_exact':before['footprints']==after['footprints'],'length':length,'vias':sum(t.GetClass()=='PCB_VIA' for t in b.GetTracks()),'old_vias_exact':all(after['tracks'].get(u)==t for u,t in before['tracks'].items() if t['class']=='PCB_VIA')}
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
