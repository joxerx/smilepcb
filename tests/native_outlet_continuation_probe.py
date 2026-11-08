"""Native first-contact regression; each invocation owns one board."""
import json
from pathlib import Path
import sys
import time

from smilepcb.native_worker import p, snapshot
from smilepcb.keyboard_process import apply_native_rules, COMPACT_VIAS
from smilepcb.keyboard_routing.controller_escape import _prove
from smilepcb.keyboard_routing.generation import select_generation_profile
from smilepcb.keyboard_routing.grid_search import search_boundary
from smilepcb.keyboard_routing.interface_launches import reserve_launches
from smilepcb.keyboard_routing.local_router import LocalRouter, vec, xy
from smilepcb.routing_quality import using_quality_profile

case=sys.argv[1];out=Path(sys.argv[2])
select_generation_profile('dsn-process-v2');quality=using_quality_profile('turns');quality.__enter__()
board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,COMPACT_VIAS)
board.Add(p.NETINFO_ITEM(board,'signal'))
corners=[(0,0),(30000000,0),(30000000,30000000),(0,30000000)]
for a,z in zip(corners,corners[1:]+corners[:1]):
    edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(vec(a));edge.SetEnd(vec(z))
    edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);board.Add(edge)
origin=(15000000,15000000)
direction=case.split('-')[-1]
turn={'east':0,'south':1,'west':2,'north':3}.get(direction,0)
def point(u,v):
    for _ in range(turn):u,v=-v,u
    return origin[0]+u,origin[1]+v
def local(q):
    u,v=q[0]-origin[0],q[1]-origin[1]
    for _ in range(turn):u,v=v,-u
    return u,v
chip=p.FOOTPRINT(board);chip.SetReference('CUSTOM');chip.SetPosition(vec(origin));board.Add(chip)
pad=p.PAD(chip);pad.SetNumber('out');pad.SetNet(board.FindNet('signal'));pad.SetAttribute(p.PAD_ATTRIB_SMD)
pad.SetShape(p.PAD_SHAPE_RECT);pad.SetSize(vec((300000,300000)));pad.SetPosition(vec(origin))
layers=p.LSET();layers.AddLayer(p.F_Cu);pad.SetLayerSet(layers);chip.Add(pad)
corner=975000 if case=='search-wide' else 1000000
if case=='search-via':
    board.Add(p.NETINFO_ITEM(board,'guard'))
    wall=p.PCB_TRACK(board);wall.SetStart(vec(point(1500000,600000)));wall.SetEnd(vec(point(1500000,1400000)))
    wall.SetLayer(p.F_Cu);wall.SetWidth(150000);wall.SetNet(board.FindNet('guard'));board.Add(wall)
before=snapshot(board)
class Dogleg:
    def accepts_path(self,path):
        # A single 45-degree bend immediately before the first outside grid
        # point. The legal route can keep going after its first 125 um tail.
        for a,z in zip(path,path[1:]):
            a,z=local(a),local(z)
            if not (all(u==v and 0<=u<=corner for u,v in (a,z)) or
                    all(v==corner and corner<=u<=2000000 for u,v in (a,z))):return False
        return True
class Router(LocalRouter):
    def via_clear(self,q,net):
        return case=='search-via' and q==point(corner,corner) and super().via_clear(q,net)
world=Router(board,deadline=time.monotonic()+5,compact=True,track_width_nm=150000,path_policy=Dogleg())
if case.startswith('search-'):
    corners=[point(-2000000,-2000000),point(corner,2000000)]
    bounds=(min(q[0] for q in corners),min(q[1] for q in corners),max(q[0] for q in corners),max(q[1] for q in corners))
    world.grid_step=162500 if case=='search-wide' else 125000
    world.search_budget_s=3;world.search_expansion_limit=3000
    options={'continuation_width_nm':200000} if case=='search-wide' else {}
    connected=search_boundary(world,[(origin,p.F_Cu)],bounds,'signal',**options)
    if not connected:
        out.write_text(json.dumps({'connected':False,'search':world.last_search}));raise SystemExit(0)
    plan=world.last_plan
else:
    length={'proof-short':125000,'proof-wide':162500,'proof-fixed':125000,'proof-long':250000}[case]
    path=[origin,point(1000000,1000000),point(1000000+length,1000000)]
    world.add_path(path,p.F_Cu,'signal');plan={'legs':[(p.F_Cu,path)],'vias':[]}
layer,path=plan['legs'][-1];a,z=path[-2:]
items={t.m_Uuid.AsString():t for t in board.GetTracks()}
terminal=next(u for u in world.added if items[u].GetClass()=='PCB_TRACK' and {xy(items[u].GetStart()),xy(items[u].GetEnd())}=={a,z})
via=next((u for u in world.added if isinstance(items[u],p.PCB_VIA)),None)
port={'net':'signal','pad_ids':[pad.m_Uuid.AsString()],'kind':'via' if via else 'same_layer',
      'via_id':via,'via_nm':xy(items[via].GetPosition()) if via else None,
      'exit_nm':z,'exit_layer':layer,'heading_origin_nm':a,'terminal_id':terminal,'added_ids':list(world.added)}
future=tuple(z[i]+((z[i]>a[i])-(z[i]<a[i]))*500000 for i in (0,1))
checks={}
for width in (150000,200000):
    shape=p.SHAPE_SEGMENT(vec(z),vec(future),width)
    with reserve_launches([port]):
        scoped=LocalRouter(board,deadline=time.monotonic()+3,compact=True,track_width_nm=width)
        checks[str(width)]=list(scoped.collisions(shape,layer,'signal'))
proof_error=None
try:
    if case=='proof-fixed':
        with reserve_launches([port]):_prove(board,snapshot(board),{},{},[port],time.monotonic()+3,173000)
    else:_prove(board,before,{'owner':port},{'owner':plan},[],time.monotonic()+3,173000)
except ValueError as error:proof_error=str(error)
out.write_text(json.dumps({'connected':True,'terminal_length_nm':sum(abs(z[i]-a[i]) for i in (0,1)),
    'continuation_collisions':checks,'proof_error':proof_error,'plan':plan},sort_keys=True))
