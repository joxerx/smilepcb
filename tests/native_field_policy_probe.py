"""Exercise admission and obstacle routing in real native geometry."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_routing.field_policy import FieldPathPolicy
from smilepcb.keyboard_routing.grid_search import valid_plan,search

def v(x,y):return p.VECTOR2I(round(x*1e6),round(y*1e6))
def board(hole=False):
 b=p.BOARD();b.Add(p.NETINFO_ITEM(b,'signal'))
 points=[(0,0),(20,0),(20,20),(0,20),(0,0)]
 for a,z in zip(points,points[1:]):
  edge=p.PCB_SHAPE(b);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetLayer(p.Edge_Cuts);edge.SetStart(v(*a));edge.SetEnd(v(*z));edge.SetWidth(50000);b.Add(edge)
 if hole:
  f=p.FOOTPRINT(b);f.SetReference('H');b.Add(f);d=p.PAD(f);d.SetAttribute(p.PAD_ATTRIB_NPTH);d.SetShape(p.PAD_SHAPE_CIRCLE);d.SetSize(v(4,4));d.SetDrillSize(v(4,4));d.SetLayerSet(p.LSET.AllCuMask());d.SetPosition(v(10,10));f.Add(d)
 return b
b=board();router=LocalRouter(b,compact=True);router.path_policy=FieldPathPolicy('column');a=(4000000,4000000);z=(8000000,12000000);path=router.leg(a,z,p.F_Cu,'signal');report={'simple_path':path,'simple_column_legal':path is not None and FieldPathPolicy('column').accepts_path(path),'shelf_plan_rejected':not valid_plan(router,[(p.F_Cu,[a,(8000000,4000000)])],[],'signal')}
try:LocalRouter(b,compact=True,path_policy=FieldPathPolicy('column'));report['constructor_accepts']=True
except TypeError:report['constructor_accepts']=False
keep=len(list(b.GetTracks()));rejected=False
try:router.add_path([a,(8000000,4000000)],p.F_Cu,'signal')
except ValueError:rejected=True
report['invalid_emission_rejected_without_mutation']=rejected and len(list(b.GetTracks()))==keep
ordinary=LocalRouter(board(),compact=True);report['ordinary_routing_unchanged']=ordinary.leg(a,z,p.F_Cu,'signal')==[a,z]
b=board(True);router=LocalRouter(b,compact=True,deadline=time.monotonic()+6);router.path_policy=FieldPathPolicy('column');router.grid_step=500000;router.search_budget_s=4;router.heuristic_weight=2;router.via_cost_nm=1000000
okay=search(router,(10000000,4000000),(10000000,16000000),[p.F_Cu],[p.F_Cu],'signal');tracks=[t for t in b.GetTracks() if t.GetClass()=='PCB_TRACK'];report['obstacle_connected']=okay;report['all_graph_segments_column_legal']=bool(tracks) and all(FieldPathPolicy('column').accepts_path([(t.GetStart().x,t.GetStart().y),(t.GetEnd().x,t.GetEnd().y)]) for t in tracks);report['obstacle_search']=getattr(router,'last_search',None)
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);p.SaveBoard(str(out/'obstacle.kicad_pcb'),b);(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
