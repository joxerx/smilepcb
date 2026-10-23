"""STM32 recipe support: extract controller. Native checks are required."""
import sys,json,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from smilepcb.native_worker import p, snapshot

root=Path(sys.argv[1]);source=root/'sources/le-chiffre/stm32_hotswap_chiffre.kicad_pcb'
before=hashlib.sha256(source.read_bytes()).hexdigest();b=p.LoadBoard(str(source))
original_arcs={uid:v for uid,v in snapshot(b)['tracks'].items() if v['class']=='PCB_ARC'}
refs={'U1','C1','C2','C3','C4','C5','C6','C7'}
keep=[f for f in b.GetFootprints() if f.GetReference() in refs]
boxes=[]
for f in keep:
 c=f.GetCourtyard(f.GetLayer());a=c.BBox() if c.OutlineCount() else f.GetBoundingBox(False,False)
 boxes.append((a.GetLeft(),a.GetTop(),a.GetRight(),a.GetBottom()))
box=[min(a[i] for a in boxes)-1000000 if i<2 else max(a[i] for a in boxes)+1000000 for i in range(4)]
def clip(a,z):
 lo,hi=0.,1.;dx,dy=z.x-a.x,z.y-a.y
 for v,q in [(-dx,a.x-box[0]),(dx,box[2]-a.x),(-dy,a.y-box[1]),(dy,box[3]-a.y)]:
  if not v:
   if q<0:return None
  elif v<0:lo=max(lo,q/v)
  else:hi=min(hi,q/v)
 if hi<=lo:return None
 return p.VECTOR2I(round(a.x+lo*dx),round(a.y+lo*dy)),p.VECTOR2I(round(a.x+hi*dx),round(a.y+hi*dy))
nets={d.GetNetname() for f in keep for d in f.Pads() if d.GetNumber()}
for f in list(b.GetFootprints()):
 if f.GetReference() not in refs:b.Delete(f)
for t in list(b.GetTracks()):
 if t.GetNetname() not in nets:b.Delete(t);continue
 if isinstance(t,p.PCB_VIA):
  a=t.GetPosition()
  if not(box[0]<=a.x<=box[2] and box[1]<=a.y<=box[3]):b.Delete(t)
 elif isinstance(t,p.PCB_ARC):
  a=t.GetBoundingBox()
  if a.GetRight()<box[0] or a.GetLeft()>box[2] or a.GetBottom()<box[1] or a.GetTop()>box[3]:b.Delete(t)
 else:
  segment=clip(t.GetStart(),t.GetEnd())
  if segment is None:b.Delete(t)
  else:t.SetStart(segment[0]);t.SetEnd(segment[1])
for z in list(b.Zones()):b.Delete(z)
for shape in list(b.GetDrawings()):b.Delete(shape)
for a,z in zip([(box[0],box[1]),(box[2],box[1]),(box[2],box[3]),(box[0],box[3])],[(box[2],box[1]),(box[2],box[3]),(box[0],box[3]),(box[0],box[1])]):
 s=p.PCB_SHAPE(b);s.SetShape(p.SHAPE_T_SEGMENT);s.SetStart(p.VECTOR2I(*a));s.SetEnd(p.VECTOR2I(*z));s.SetLayer(p.Edge_Cuts);s.SetWidth(50000);b.Add(s)
b.BuildConnectivity();out=root/(sys.argv[2] if len(sys.argv)>2 else 'core');out.mkdir(exist_ok=False);p.SaveBoard(str(out/'core.kicad_pcb'),b)
from smilepcb.keyboard_routing.safety import require_via_land_clearance
try:require_via_land_clearance(b);land='pass'
except Exception as e:land=str(e)
state=snapshot(b)
assert all(v==original_arcs[uid] for uid,v in state['tracks'].items() if v['class']=='PCB_ARC'),'Extraction changed a source arc'
result={'source_sha256':before,'refs':sorted(refs),'box_mm':[v/1e6 for v in box],
 'size_mm':[(box[2]-box[0])/1e6,(box[3]-box[1])/1e6],
 'tracks':len(list(b.GetTracks())),'via_land':land,'source_unchanged':hashlib.sha256(source.read_bytes()).hexdigest()==before,
 'source_arcs_kept_exactly':sum(v['class']=='PCB_ARC' for v in state['tracks'].values()),'snapshot':state}
(root/('extraction-'+out.name+'.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='snapshot'}))
