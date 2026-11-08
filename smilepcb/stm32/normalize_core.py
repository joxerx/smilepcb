"""STM32 recipe support: normalize core. Native checks are required."""
import sys,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_routing.local_router import LocalRouter
from smilepcb.keyboard_routing.native_graph import route_components,copper_graph
from smilepcb.keyboard_routing.safety import require_via_land_clearance
from smilepcb.keyboard_routing.controller_access import prune_single_terminal_stubs,release_core_copper
from collections import defaultdict
base=Path(sys.argv[1]);source=base/(sys.argv[3] if len(sys.argv)>3 else 'core')/'core.kicad_pcb';out=base/(sys.argv[2] if len(sys.argv)>2 else 'normalized-core');out.mkdir(exist_ok=False);b=p.LoadBoard(str(source));before=snapshot(b);started=time.monotonic()
settings=b.GetDesignSettings();settings.m_MinClearance=p.FromMM(.15);settings.m_CopperEdgeClearance=p.FromMM(.2);settings.m_TrackMinWidth=p.FromMM(.15);settings.m_MinThroughDrill=p.FromMM(.3);settings.m_ViasMinAnnularWidth=p.FromMM(.15);settings.m_ViasMinSize=p.FromMM(.6)
reroute='--reroute-core' in sys.argv[4:]
pruned=(release_core_copper if reroute else prune_single_terminal_stubs)(b,protected_nets={'D_N','D_P'})
adjusted=[]
for t in b.GetTracks():
 if t.GetNetname() in ('D_N','D_P') and not isinstance(t,p.PCB_VIA):
  adjusted.append(t.m_Uuid.AsString());t.SetWidth(p.FromMM(.2))
# The donor's global supplies leave the clipped region. Connect their local
# pad groups once, before trying any keyboard. The original source is read-only.
points=[q for edge in b.GetDrawings() if edge.GetLayer()==p.Edge_Cuts for q in (edge.GetStart(),edge.GetEnd())]
center=p.VECTOR2I(round((min(q.x for q in points)+max(q.x for q in points))/2),round((min(q.y for q in points)+max(q.y for q in points))/2))
for edge in b.GetDrawings():
 if edge.GetLayer()==p.Edge_Cuts:
  # A probe outline, expanded uniformly, permits routing around the source core.
  for method in ('GetStart','GetEnd'):
   q=getattr(edge,method)();q=p.VECTOR2I(q.x+(-2000000 if q.x<center.x else 2000000),q.y+(-2000000 if q.y<center.y else 2000000));getattr(edge,method.replace('Get','Set'))(q)
router=LocalRouter(b,deadline=started+55,compact=True);router.grid_step=250000;router.connection_budget_s=5;router.search_budget_s=5
joined=0 if reroute else route_components(b,router,{'+3V3','GND'});land=require_via_land_clearance(b)
g=copper_graph(b,{'+3V3','GND','NRST'},started+60);groups=defaultdict(set)
for i,node in enumerate(g['nodes']):
 if isinstance(node['item'],p.PAD) and node['item'].GetNumber():groups[node['net']].add(g['find'](i))
report={'core_routing_policy':'reroute except protected USB' if reroute else 'preserve internal connections','joined':joined,'remaining_core_gaps':{name:len(rows)-1 for name,rows in groups.items() if len(rows)>1},'adjusted_usb_width_mm':.2,'adjusted_source_track_ids':adjusted,'released_copper':pruned,'added_copper_ids':router.added,'via_land':land,'elapsed_s':time.monotonic()-started}
p.SaveBoard(str(out/'core.kicad_pcb'),b);(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
