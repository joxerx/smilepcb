import sys,json,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.visual_polish import polish_columns,prune_duplicates
from smilepcb.keyboard_routing.local_router import LocalRouter
M=1000000
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
def fixture(obstacle=False):
 b=p.BOARD();b.SetCopperLayerCount(2);r=b.GetDesignSettings();r.m_MinClearance=150000;r.m_TrackMinWidth=150000
 for net in ('bus','foreign'):b.Add(p.NETINFO_ITEM(b,net))
 for a,z in zip([(0,0),(20,0),(20,20),(0,20)],[(20,0),(20,20),(0,20),(0,0)]):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);e.SetStart(p.VECTOR2I(*(x*M for x in a)));e.SetEnd(p.VECTOR2I(*(x*M for x in z)));b.Add(e)
 for i,(q,net) in enumerate([((3,3),'bus'),((9,15),'bus')]+([((6,7),'foreign')] if obstacle else [])):
  f=p.FOOTPRINT(b);f.SetReference('P'+str(i));b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetPosition(p.VECTOR2I(*(x*M for x in q)));d.SetSize(p.VECTOR2I(M,M));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);ls=p.LSET();ls.AddLayer(p.B_Cu);d.SetLayerSet(ls);d.SetNet(b.FindNet(net));f.Add(d)
 for a,z in [((3,3),(9,3)),((9,3),(9,15))]:
  t=p.PCB_TRACK(b);t.SetStart(p.VECTOR2I(*(x*M for x in a)));t.SetEnd(p.VECTOR2I(*(x*M for x in z)));t.SetWidth(150000);t.SetLayer(p.B_Cu);t.SetNet(b.FindNet('bus'));b.Add(t)
 return b
result={}
for label in ('simple','obstacle','expired','partial-fault','duplicates','bridge','hole-bypass','grouping'):
 b=fixture(label=='obstacle')
 if label=='bridge':
  t=p.PCB_TRACK(b);t.SetStart(p.VECTOR2I(1*M,7*M));t.SetEnd(p.VECTOR2I(19*M,7*M));t.SetWidth(150000);t.SetLayer(p.B_Cu);t.SetNet(b.FindNet('foreign'));t.SetLocked(True);b.Add(t)
 if label=='grouping':
  for t in list(b.GetTracks()):b.Delete(t)
  for fp in b.GetFootprints():
   for d in fp.Pads():d.SetPosition(p.VECTOR2I((2 if fp.GetReference()=='P0' else 18)*M,12*M))
  for net,y,x0,x1 in [('bus',12,2,18),('foreign',9,1,19)]:
   t=p.PCB_TRACK(b);t.SetStart(p.VECTOR2I(x0*M,y*M));t.SetEnd(p.VECTOR2I(x1*M,y*M));t.SetWidth(150000);t.SetLayer(p.B_Cu);t.SetNet(b.FindNet(net));t.SetLocked(net=='foreign');b.Add(t)
 if label=='hole-bypass':
  for q,size in [((3,5),1600000),((6,6),3000000)]:
   fp=p.FOOTPRINT(b);fp.SetReference('hole-'+str(q));b.Add(fp);d=p.PAD(fp);d.SetAttribute(p.PAD_ATTRIB_NPTH);d.SetShape(p.PAD_SHAPE_CIRCLE);d.SetSize(p.VECTOR2I(size,size));d.SetDrillSize(p.VECTOR2I(size,size));d.SetPosition(p.VECTOR2I(*(v*M for v in q)));d.SetLayerSet(p.LSET.AllCuMask());fp.Add(d)
 before=snapshot(b);original=LocalRouter.index
 if label=='partial-fault':
  def fail(self,item):
   if item.GetClass()=='PCB_TRACK' and item.m_Uuid.AsString() not in before['tracks']:raise RuntimeError('Injected failure after board.Add')
   return original(self,item)
  LocalRouter.index=fail
 try:
  if label=='duplicates':
   t=next(iter(b.GetTracks()));t.SetLocked(True);q=t.Duplicate();q.SetUuid(p.KIID());q.SetLocked(False);b.Add(q);before=snapshot(b);report=prune_duplicates(b,protected_ids={t.m_Uuid.AsString()},deadline=time.monotonic()+10)
  elif label=='grouping':report=polish_columns(b,column_nets={'bus','foreign'},protected_ids=(),mode='feeders',minimum_span_nm=2000000,deadline=time.monotonic()+20)
  else:report=polish_columns(b,column_nets={'bus'},protected_ids=(),deadline=time.monotonic()-1 if label=='expired' else time.monotonic()+20)
 except Exception as error:report={'fault':type(error).__name__+': '+str(error)}
 finally:LocalRouter.index=original
 after=snapshot(b);result[label]={'report':report,'unchanged':before==after,'footprints_exact':before['footprints']==after['footprints'],'horizontal_nm':sum(abs(t['start'][0]-t['end'][0]) for t in after['tracks'].values() if t['class']=='PCB_TRACK' and t['start'][1]==t['end'][1]),'bus_horizontal_y':[t['start'][1] for t in after['tracks'].values() if t['class']=='PCB_TRACK' and t['net']=='bus' and t['start'][1]==t['end'][1]],'objects':len(after['tracks']),'vias':sum(t['class']=='PCB_VIA' for t in after['tracks'].values())}
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
