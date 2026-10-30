"""Exercise the actual recipe packing oracle with native library footprints."""
import ast,json,sys
from pathlib import Path
from smilepcb import native_worker as w
p=w.p
root=Path(__file__).resolve().parents[1];out=Path(sys.argv[1]);out.mkdir(exist_ok=True,parents=True)
tree=ast.parse((root/'recipes/stm32/place.py').read_text())
functions=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef)],type_ignores=[])
def v(x,y):return p.VECTOR2I(round(x),round(y))
def fp(board,ref,xy,back=False):
 f=p.FootprintLoad(str(w.SUPPORT/'footprints/Package_TO_SOT_SMD.pretty'),'SOT-23-5')
 f.SetReference(ref);f.Reference().SetVisible(False);f.Value().SetVisible(False)
 f.SetPosition(w.point(xy));board.Add(f)
 if back:f.Flip(f.GetPosition(),p.FLIP_DIRECTION_LEFT_RIGHT)
 f.BuildCourtyardCaches();return f
report={}
for name,xy,back in [('overlap',(2.5,3.5),False),('separated',(3,4),False),('opposite',(2.5,3.5),True)]:
 b=p.BOARD();a=fp(b,'U1',(20,20));z=fp(b,'U2',(20+xy[0],20+xy[1]),back)
 board_region=p.SHAPE_POLY_SET();board_region.NewOutline()
 for x,y in ((0,0),(100000000,0),(100000000,100000000),(0,100000000)):board_region.Append(x,y)
 scope={'p':p,'cells':{},'cell':10000000,'rules':b.GetDesignSettings(),'vector':v,'board_region':board_region,'outline':(0,0,100000000,100000000)}
 exec(compile(functions,'recipe-placement-oracle','exec'),scope)
 scope['add_obstacles'](a)
 before=out/(name+'-before.kicad_pcb');after=out/(name+'-after.kicad_pcb');assert p.SaveBoard(str(before),b,True)
 accepted=scope['clear']([z])
 assert p.SaveBoard(str(after),b,True)
 ink_a=[g.GetEffectiveShape() for g in a.GraphicalItems() if isinstance(g,p.PCB_SHAPE) and g.GetLayer()==p.F_SilkS]
 ink_z=[g.GetEffectiveShape() for g in z.GraphicalItems() if isinstance(g,p.PCB_SHAPE) and g.GetLayer()==p.F_SilkS]
 report[name]={'accepted':accepted,'source_unchanged':before.read_bytes()==after.read_bytes(),
  'courtyards_overlap':bool(p.SHAPE.Collide(a.GetCourtyard(p.F_Cu),z.GetCourtyard(p.F_Cu),0)) if not back else False,
  'native_ink_overlap':any(p.SHAPE.Collide(s,t,0) for s in ink_a for t in ink_z)}
 # Ink must never become a copper-routing obstacle in the packing oracle.
 if name=='overlap':
  track=p.PCB_TRACK(b);track.SetStart(w.point((20,20)));track.SetEnd(w.point((20,21)));track.SetWidth(150000);track.SetLayer(p.B_Cu)
  report['opposite_copper_accepted']=scope['clear']([track])
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
