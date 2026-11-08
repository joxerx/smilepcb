"""A finer broad phase must preserve native answers and reduce exact tests."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_process import apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing.generation import generation_profile
b=p.BOARD();b.SetCopperLayerCount(2);apply_native_rules(b,COMPACT_VIAS)
for name in ('fixed','query'):b.Add(p.NETINFO_ITEM(b,name))
pts=[(0,0),(30000000,0),(30000000,20000000),(0,20000000)]
for a,z in zip(pts,pts[1:]+pts[:1]):
 e=p.PCB_SHAPE(b);e.SetShape(p.SHAPE_T_SEGMENT);e.SetStart(p.VECTOR2I(*a));e.SetEnd(p.VECTOR2I(*z));e.SetLayer(p.Edge_Cuts);e.SetWidth(50000);b.Add(e)
f=p.FOOTPRINT(b);f.SetReference('dense');b.Add(f)
for row in range(8):
 for col in range(16):
  d=p.PAD(f);d.SetNumber(str(row*16+col+1));d.SetNet(b.FindNet('fixed'));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT);d.SetSize(p.VECTOR2I(200000,200000));d.SetPosition(p.VECTOR2I(5000000+col*500000,5000000+row*500000));ls=p.LSET();ls.AddLayer(p.F_Cu);d.SetLayerSet(ls);f.Add(d)
# Long copper and drills exercise index padding and spanning objects too.
t=p.PCB_TRACK(b);t.SetStart(p.VECTOR2I(2000000,11000000));t.SetEnd(p.VECTOR2I(27000000,11000000));t.SetWidth(300000);t.SetNet(b.FindNet('fixed'));t.SetLayer(p.B_Cu);b.Add(t)
v=p.PCB_VIA(b);v.SetPosition(p.VECTOR2I(16000000,11000000));v.SetWidth(500000);v.SetDrill(200000);v.SetLayerPair(p.F_Cu,p.B_Cu);v.SetNet(b.FindNet('fixed'));b.Add(v)
queries=[]
for i in range(160):
 a=(4000000+(i*719123)%14500000,4000000+(i*337021)%10000000);z=(a[0]+125000,a[1]+(125000 if i%2 else 0))
 queries.append((p.SHAPE_SEGMENT(p.VECTOR2I(*a),p.VECTOR2I(*z),150000),p.F_Cu if i%3 else p.B_Cu))
before=snapshot(b);original=p.SHAPE.Collide;result={}
with generation_profile('dsn-safe-v1'):
 for cell in (4000000,1000000):
  router=LocalRouter(b,compact=True,track_width_nm=150000,spatial_cell_nm=cell);counter=[0]
  def counted(*a,**kw):counter[0]+=1;return original(*a,**kw)
  p.SHAPE.Collide=counted
  try:answers=[sorted(set(router.collisions(shape,layer,'query'))) for shape,layer in queries]
  finally:p.SHAPE.Collide=original
  result[str(cell)]={'answers':answers,'native_shape_tests':counter[0]}
result['source_exact']=snapshot(b)==before
Path(sys.argv[1]).write_text(json.dumps(result,indent=2)+'\n')
