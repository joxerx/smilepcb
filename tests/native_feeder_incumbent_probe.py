"""Fault-injected negotiation/restoration; admission uses real native copper."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing import feeder_repair,native_graph
from smilepcb.keyboard_routing.feeder_repair import repair_feeders,pending_matrix
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);report={}
def make():
 b=p.BOARD()
 for a,z in (((0,0),(30000000,0)),((30000000,0),(30000000,15000000)),((30000000,15000000),(0,15000000)),((0,15000000),(0,0))):
  e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(p.VECTOR2I(*a));e.SetEnd(p.VECTOR2I(*z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
 b.SetCopperLayerCount(2);b.GetDesignSettings().m_TrackMinWidth=150000;b.GetDesignSettings().m_MinClearance=150000
 for i,n in enumerate(('a','b','c','fixed')):
  b.Add(p.NETINFO_ITEM(b,n))
  for j,x in enumerate((1000000,11000000)):
   f=p.FOOTPRINT(b);f.SetReference(n+str(j));b.Add(f);d=p.PAD(f);d.SetNumber('1');d.SetPosition(p.VECTOR2I(x,1000000+i*2000000));d.SetSize(p.VECTOR2I(500000,500000));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);layers=p.LSET();layers.AddLayer(p.F_Cu);d.SetLayerSet(layers);d.SetNet(b.FindNet(n));f.Add(d)
  if n!='c':add(b,n,i)
 return b
def add(b,n,i):
 t=p.PCB_TRACK(b);t.SetStart(p.VECTOR2I(1000000,1000000+i*2000000));t.SetEnd(p.VECTOR2I(11000000,1000000+i*2000000));t.SetLayer(p.F_Cu);t.SetWidth(150000);t.SetNet(b.FindNet(n));t.SetLocked(n=='fixed');b.Add(t);return t.m_Uuid.AsString()
old_negotiate,old_route=native_graph.negotiate_routes,native_graph.route_components
try:
 for case in ('worse','timeout','improved'):
  b=make();before=snapshot(b);protected={u for u,t in before['tracks'].items() if t['net']=='fixed'}
  def trial(board,nets,protected,deadline,**kwargs):
   removed=[]
   if case!='improved':
    for t in list(board.GetTracks()):
     if t.GetNetname() in ('a','b'):removed.append(t.m_Uuid.AsString());board.Delete(t)
   if case=='timeout':raise TimeoutError('Injected restore/negotiation deadline after real mutation')
   uid=add(board,'c',2)
   return {'connected':1,'removed':removed,'added':[uid]}
  def failed_restore(board,router,*args,**kwargs):
   router.graph_diagnostic={};return 0
  native_graph.negotiate_routes=trial;native_graph.route_components=failed_restore
  error=None
  try:r=repair_feeders(b,{'a','b','c'},protected,deadline=time.monotonic()+60)
  except (ValueError,TimeoutError) as e:r=[];error=str(e)
  after=snapshot(b);report[case]={'history':r,'error':error,'exact':after==before,'pending':pending_matrix(b,{'a','b','c'},time.monotonic()+5),'source_exact':all(after['tracks'].get(u)==before['tracks'][u] for u in protected)}
finally:native_graph.negotiate_routes,native_graph.route_components=old_negotiate,old_route
(out/'report.json').write_text(json.dumps(report,indent=2))
