"""Native synthetic geometry regression probe; requires KiCad's interpreter."""
import json,sys
from pathlib import Path
from smilepcb.native_worker import p
from smilepcb.routing_style import Clearance
from smilepcb.keyboard_routing.local_router import LocalRouter

OUT=Path(sys.argv[1]);OUT.mkdir(parents=True,exist_ok=True)
def v(x,y):return p.VECTOR2I(p.FromMM(x),p.FromMM(y))
def base():
 b=p.BOARD();b.GetDesignSettings().m_TrackMinWidth=150000
 for a,z in [((0,0),(30,0)),((30,0),(30,30)),((30,30),(0,30)),((0,30),(0,0))]:
  s=p.PCB_SHAPE(b);s.SetShape(p.SHAPE_T_SEGMENT);s.SetStart(v(*a));s.SetEnd(v(*z));s.SetLayer(p.Edge_Cuts);s.SetWidth(50000);b.Add(s)
 for name in ("signal","strict","ordinary"):b.Add(p.NETINFO_ITEM(b,name))
 return b
def inspect(b,net="signal",x=10):
 a,z=v(x,9),v(x,11);width=max(150000,b.GetDesignSettings().m_TrackMinWidth);shape=p.SHAPE_SEGMENT(a,z,width);r={}
 for name,call in [("simple",lambda:Clearance(b).clear((a.x,a.y),(z.x,z.y),width,p.F_Cu,net)),("local",lambda:LocalRouter(b,track_width_nm=width).clear(shape,p.F_Cu,net))]:
  try:r[name]=call()
  except Exception as e:r[name]={"error":type(e).__name__,"message":str(e)}
 return r
report={}
for kind in ("board_shape","footprint_shape","board_text","footprint_text","footprint_field","silkscreen","opposite_face","registration_target"):
 b=base()
 if kind=="registration_target":
  g=p.PCB_TARGET(b);g.SetPosition(v(10,10));g.SetSize(1000000);g.SetWidth(100000);g.SetLayer(p.Edge_Cuts);b.Add(g)
 elif kind in ("board_text","footprint_text","footprint_field"):
  if kind.startswith("footprint"):
   fp=p.FOOTPRINT(b);fp.SetReference("F1");b.Add(fp)
   if kind=="footprint_field":g=fp.Reference()
   else:g=p.PCB_TEXT(fp);fp.Add(g)
  else:g=p.PCB_TEXT(b);b.Add(g)
  g.SetText("+");g.SetTextSize(v(3,3));g.SetTextThickness(300000);g.SetPosition(v(10,10));g.SetLayer(p.F_Cu);g.SetVisible(True)
 else:
  owner=b
  if kind=="footprint_shape":owner=p.FOOTPRINT(b);owner.SetReference("G1");b.Add(owner)
  g=p.PCB_SHAPE(owner);g.SetShape(p.SHAPE_T_SEGMENT);g.SetStart(v(10,8));g.SetEnd(v(10,12));g.SetWidth(200000);g.SetLayer(p.F_SilkS if kind=="silkscreen" else p.B_Cu if kind=="opposite_face" else p.F_Cu);owner.Add(g)
 before=OUT/(kind+".kicad_pcb");after=OUT/(kind+"-after.kicad_pcb")
 assert p.SaveBoard(str(before),b,True)
 b=p.LoadBoard(str(before))
 report[kind]=inspect(b)
 assert p.SaveBoard(str(after),b,True)
 report[kind]["source_unchanged"]=before.read_bytes()==after.read_bytes()
 # A concrete stroke intersection avoids an accidental text whitespace probe.
 if kind in ("board_text","footprint_text","footprint_field"):
  effective=g.GetEffectiveShape(p.F_Cu);report[kind]["native_shape_crosses_probe"]=p.SHAPE.Collide(p.SHAPE_SEGMENT(v(10,9),v(10,11),150000),effective,0)

d=OUT/"rules";d.mkdir(exist_ok=True);path=d/"fixture.kicad_pcb";b=base()
fp=p.FOOTPRINT(b);fp.SetReference("J1");b.Add(fp)
for i,name in enumerate(("strict","ordinary")):
 layers=p.LSET();layers.AddLayer(p.F_Cu)
 pad=p.PAD(fp);pad.SetNumber(str(i+1));pad.SetAttribute(p.PAD_ATTRIB_SMD);pad.SetShape(p.PAD_SHAPE_RECT);pad.SetLayerSet(layers);pad.SetPosition(v(2+i*3,2));pad.SetSize(v(1,1));pad.SetNet(b.FindNet(name));fp.Add(pad)
t=p.PCB_TRACK(b);t.SetStart(v(10,8));t.SetEnd(v(10,12));t.SetWidth(200000);t.SetLayer(p.F_Cu);t.SetNet(b.FindNet("ordinary"));b.Add(t)
assert p.SaveBoard(str(path),b,True)
pro={"meta":{"version":1},"board":{"design_settings":{"rules":{"min_clearance":0.15,"min_track_width":0.15,"min_copper_edge_clearance":0.2}}},"net_settings":{"classes":[{"name":"Default","clearance":0.15,"track_width":0.15,"via_diameter":0.6,"via_drill":0.3},{"name":"Strict","clearance":0.508,"track_width":0.15,"via_diameter":0.6,"via_drill":0.3}],"netclass_patterns":[{"netclass":"Strict","pattern":"strict"}]}}
path.with_suffix(".kicad_pro").write_text(json.dumps(pro))
loaded=p.LoadBoard(str(path));pads=[pad for fp in loaded.GetFootprints() for pad in fp.Pads()]
report["netclass"]={"own":{pad.GetNetname():pad.GetOwnClearance(p.F_Cu) for pad in pads},"too_close":inspect(loaded,"strict",10.4488),"clear":inspect(loaded,"strict",10.775)}
report["netclass"]["same_net"]=inspect(loaded,"ordinary",10)
# A stricter routed net must search across the foreign object's spatial bucket.
for pad in pads:
 if pad.GetNetname()=="strict":pad.SetLocalClearance(800000)
u=p.PCB_TRACK(loaded);u.SetStart(v(7.2,8));u.SetEnd(v(7.2,12));u.SetWidth(200000);u.SetLayer(p.F_Cu);u.SetNet(loaded.FindNet("ordinary"));loaded.Add(u)
report["netclass"]["bucket_boundary"]=inspect(loaded,"strict",8.1)
# Native DRC witness for the original 0.2738 mm edge gap.
for pad in pads:pad.SetLocalClearance(0)
w=p.PCB_TRACK(loaded);w.SetStart(v(10.4488,9));w.SetEnd(v(10.4488,11));w.SetWidth(150000);w.SetLayer(p.F_Cu);w.SetNet(loaded.FindNet("strict"));loaded.Add(w)
assert p.SaveBoard(str(path),loaded,True)
(OUT/"report.json").write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report))
