"""Equivalent saved geometry must not choose a different return tree by insertion order."""
import hashlib,json,sys,time,uuid
from pathlib import Path
from smilepcb.native_worker import p
from smilepcb.keyboard_routing.power import connect_controller_return_tree
from smilepcb.routing_quality import using_quality_profile
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
def v(x,y):return p.VECTOR2I(p.FromMM(x),p.FromMM(y))
def make(order,tag):
 b=p.BOARD();b.Add(p.NETINFO_ITEM(b,'RETURN'));rules=b.GetDesignSettings();rules.m_MinClearance=150000;rules.m_TrackMinWidth=150000;rules.m_CopperEdgeClearance=200000;rules.m_MinThroughDrill=200000;rules.m_ViasMinSize=500000;rules.m_ViasMinAnnularWidth=150000
 rules.m_NetSettings.GetDefaultNetclass().SetClearance(150000)
 for a,z in (((0,0),(20,0)),((20,0),(20,20)),((20,20),(0,20)),((0,20),(0,0))):
  edge=p.PCB_SHAPE(b);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(v(*a));edge.SetEnd(v(*z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);b.Add(edge)
 positions={'A':(5,5),'B':(5,15),'C':(15,5),'D':(15,15)}
 for serial,ref in enumerate(order,1):
  fp=p.FOOTPRINT(b);fp.SetReference(ref);fp.SetUuid(p.KIID(str(uuid.UUID(int=serial))));b.Add(fp)
  d=p.PAD(fp);d.SetNumber('1');d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(v(.5,.5));d.SetPosition(v(*positions[ref]));d.SetNet(b.FindNet('RETURN'));layers=p.LSET();layers.AddLayer(p.F_Cu);d.SetLayerSet(layers);fp.Add(d)
 source=out/(tag+'-source.kicad_pcb');assert p.SaveBoard(str(source),b,True);digest=hashlib.sha256(source.read_bytes()).hexdigest();b=p.LoadBoard(str(source))
 input_order=[f.GetReference() for f in b.GetFootprints()]
 cfg={'plane_nets':['RETURN'],'parts':[{'ref':ref,'region':'controller'} for ref in positions]}
 with using_quality_profile('turns'):result=connect_controller_return_tree(b,cfg,budget_s=10)
 rows=[]
 for t in b.GetTracks():
  if isinstance(t,p.PCB_VIA):rows.append(['via',t.GetNetname(),t.GetPosition().x,t.GetPosition().y,t.GetWidth(),t.GetDrillValue()])
  else:
   a=(t.GetStart().x,t.GetStart().y);z=(t.GetEnd().x,t.GetEnd().y);rows.append(['track',t.GetNetname(),t.GetLayer(),t.GetWidth(),min(a,z),max(a,z)])
 assert p.SaveBoard(str(out/(tag+'-routed.kicad_pcb')),b,True)
 return {'accepted':result['accepted'],'missing':result['missing'],'input_order':input_order,'copper':sorted(rows),'source_unchanged':hashlib.sha256(source.read_bytes()).hexdigest()==digest}
a=make('ABCD','forward');b=make('DCBA','reverse')
report={'forward':a,'reverse':b,'same_copper_geometry':a['copper']==b['copper']};(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
