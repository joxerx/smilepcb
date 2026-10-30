"""Real collision admission, source preservation and repeated pair preparation."""
import json,sys,time
from pathlib import Path
from smilepcb.native_worker import p,snapshot
from smilepcb.usb_pair import try_joint_pair
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.native_graph import copper_graph

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
def v(x,y):return p.VECTOR2I(p.FromMM(x),p.FromMM(y))
def board(blocked=False):
 b=p.BOARD();b.GetDesignSettings().m_TrackMinWidth=150000;b.GetDesignSettings().m_MinClearance=150000
 for name in ('minus','plus','wall'):b.Add(p.NETINFO_ITEM(b,name))
 for a,z in (((0,0),(30,0)),((30,0),(30,30)),((30,30),(0,30)),((0,30),(0,0))):
  edge=p.PCB_SHAPE(b);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(v(*a));edge.SetEnd(v(*z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);b.Add(edge)
 for ref,contacts in (('source',(('minus',5,10),('plus',5,10.53316))),('target',(('minus',11.804493,11.073057),('plus',11.804493,12.073057)))):
  f=p.FOOTPRINT(b);f.SetReference(ref);b.Add(f)
  for number,(name,x,y) in enumerate(contacts,1):
   d=p.PAD(f);d.SetNumber(str(number));d.SetAttribute(p.PAD_ATTRIB_SMD);d.SetShape(p.PAD_SHAPE_RECT)
   layers=p.LSET();layers.AddLayer(p.F_Cu);d.SetLayerSet(layers);d.SetSize(v(.25,.25));d.SetPosition(v(x,y));d.SetNet(b.FindNet(name));f.Add(d)
 if blocked:
  t=p.PCB_TRACK(b);t.SetStart(v(8,0));t.SetEnd(v(8,30));t.SetWidth(500000);t.SetLayer(p.F_Cu);t.SetNet(b.FindNet('wall'));b.Add(t)
 return b

def join(b):
 with generation_profile('dsn-safe-v1'):
  return try_joint_pair(b,('minus','plus'),source_ref='source',target_ref='target',layer=p.F_Cu,
                        deadline=time.monotonic()+15,track_width_nm=200000)

b=board();before=snapshot(b);r=join(b);after=snapshot(b)
graph=copper_graph(b,{'minus','plus'});again=join(b)
report={'legal_narrow_pair':r is not None,'originals_preserved':before['footprints']==after['footprints'] and all(after['tracks'].get(k)==v for k,v in before['tracks'].items()),
        'connected':len(graph['groups'])==2,'octile':all(not (dx and dy) or abs(dx)==abs(dy) for t in b.GetTracks() for dx,dy in [(t.GetEnd().x-t.GetStart().x,t.GetEnd().y-t.GetStart().y)]),
        'repeat_unchanged':snapshot(b)==after and again is not None and not any(row['added_ids'] for row in again.values())}
b=board(True);before=snapshot(b);r=join(b)
report.update(blocked_rejected=r is None,blocked_unchanged=snapshot(b)==before)
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
