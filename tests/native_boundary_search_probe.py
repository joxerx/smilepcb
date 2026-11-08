"""Each invocation owns one native board, avoiding cross-board SWIG lifetime."""
import json
from pathlib import Path
import sys
import time

from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_process import apply_native_rules,COMPACT_VIAS
from smilepcb.keyboard_routing import grid_search
from smilepcb.keyboard_routing.bank_geometry import clean_port_path,port_outside_bounds
from smilepcb.keyboard_routing.generation import select_generation_profile
from smilepcb.keyboard_routing.local_router import LocalRouter,vec
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.routing_quality import using_quality_profile

case=sys.argv[1];output=Path(sys.argv[2]);search=getattr(grid_search,'search_boundary',None)
if search is None:
    output.write_text(json.dumps({'available':False,'reason':'Native boundary search is not implemented'}));raise SystemExit(0)
select_generation_profile('dsn-process-v2')
quality=using_quality_profile('turns');quality.__enter__()
board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,COMPACT_VIAS)
for net in ('signal','obstacle'):board.Add(p.NETINFO_ITEM(board,net))
corners=[(0,0),(30000000,0),(30000000,30000000),(0,30000000)]
for a,z in zip(corners,corners[1:]+corners[:1]):
    edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(vec(a));edge.SetEnd(vec(z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);board.Add(edge)
origin=(15000000,15000000);frame=(14000000,14000000,16000000,16000000)
chip=p.FOOTPRINT(board);chip.SetReference('CTRL_X');chip.SetPosition(vec(origin));chip.SetLayer(p.F_Cu);board.Add(chip)
pad=p.PAD(chip);pad.SetNumber('signal-pin');pad.SetNet(board.FindNet('signal'));pad.SetAttribute(p.PAD_ATTRIB_SMD)
pad.SetShape(p.PAD_SHAPE_RECT);pad.SetSize(vec((300000,300000)));pad.SetPosition(vec(origin));layers=p.LSET();layers.AddLayer(p.F_Cu);pad.SetLayerSet(layers);chip.Add(pad)
heading={'east':(1,0),'west':(-1,0),'north':(0,-1),'south':(0,1)}.get(case,(1,0))
if case in ('blocked','via'):
    x=15500000 if case=='blocked' else 16500000
    track=p.PCB_TRACK(board);track.SetStart(vec((x,14000000)));track.SetEnd(vec((x,16000000)))
    track.SetWidth(150000);track.SetLayer(p.F_Cu);track.SetNet(board.FindNet('obstacle'));board.Add(track)
class Ray:
    def accepts_path(self,path):
        if len(path)>1 and any(a!=z for a,z in zip(path,path[1:])) and clean_port_path(path) is None:return False
        for x,y in path:
            dx=x-origin[0];dy=y-origin[1]
            if not 0<=dx*heading[0]+dy*heading[1]<=2200000 or abs(-dx*heading[1]+dy*heading[0])>50000:return False
        return True
class Router(LocalRouter):
    def via_clear(self,point,net):return case=='via' and super().via_clear(point,net)
before=snapshot(board)
router=Router(board,deadline=time.monotonic()+5 if case!='expired' else time.monotonic()-1,
              compact=True,track_width_nm=150000,path_policy=Ray())
router.grid_step=125000;router.search_budget_s=3;router.search_expansion_limit=3000
connected=search(router,[(origin,p.F_Cu)],frame,'signal')
after=snapshot(board);report={'available':True,'connected':connected,'unchanged':after==before,
    'retained_exact':after['footprints']==before['footprints'] and all(after['tracks'].get(u)==t for u,t in before['tracks'].items())}
if connected:
    plan=router.last_plan;layer,path=plan['legs'][-1];a,z=path[-2:]
    h=tuple((z[i]>a[i])-(z[i]<a[i]) for i in (0,1));future=tuple(z[i]+h[i]*500000 for i in (0,1))
    graph=copper_graph(board,{'signal'},time.monotonic()+3,include_zones=False)
    ids={node['item'].m_Uuid.AsString():i for i,node in enumerate(graph['nodes'])}
    report.update(outside=h==heading and port_outside_bounds(z,h,frame,150000),
        angle_safe=all(clean_port_path(points) is not None for _,points in plan['legs']),
        continuation_clear=router.clear(p.SHAPE_SEGMENT(vec(z),vec(future),150000),layer,'signal'),
        all_added_connected=all(graph['find'](ids[u])==graph['find'](ids[pad.m_Uuid.AsString()]) for u in router.added),
        vias=len(plan['vias']),exit_on_opposite_face=layer==p.B_Cu)
output.write_text(json.dumps(report,sort_keys=True))
