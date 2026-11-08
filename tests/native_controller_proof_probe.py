"""Independent native owner-exclusion parity and sealed-proof integration."""
import json
from pathlib import Path
import sys
import time
from copy import deepcopy
from smilepcb.native_worker import p, snapshot
from smilepcb.keyboard_process import apply_native_rules, COMPACT_VIAS
from smilepcb.keyboard_routing.generation import select_generation_profile
from smilepcb.keyboard_routing.local_router import LocalRouter, vec
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.interface_launches import reserve_launches, current_launch_inventory, interface_identity
from smilepcb.keyboard_routing.conditional_geometry import FrozenProofRouter
import smilepcb.keyboard_routing.controller_escape as escape

select_generation_profile('dsn-process-v2')
# KiCad tracks expose no setter for local clearance. This scalar seam supplies
# per-UUID DRC contributions; all shapes, buckets and collision queries remain native.
import smilepcb.keyboard_routing.local_router as local
clearance_overrides={}
native_own_clearance=local.own_clearance
def own_clearance(item,face,minimum):
    return max(native_own_clearance(item,face,minimum),clearance_overrides.get(item.m_Uuid.AsString(),0))
local.own_clearance=own_clearance
stop=time.monotonic()+15

def fixture():
    board=p.BOARD();board.SetCopperLayerCount(2);apply_native_rules(board,COMPACT_VIAS)
    for net in ('signal','foreign'):board.Add(p.NETINFO_ITEM(board,net))
    corners=[(0,0),(20000000,0),(20000000,20000000),(0,20000000)]
    for a,z in zip(corners,corners[1:]+corners[:1]):
        edge=p.PCB_SHAPE(board);edge.SetShape(p.SHAPE_T_SEGMENT);edge.SetStart(vec(a));edge.SetEnd(vec(z));edge.SetLayer(p.Edge_Cuts);edge.SetWidth(50000);board.Add(edge)
    return board

def track(board,a,z,net='signal',gap=None):
    t=p.PCB_TRACK(board);t.SetStart(vec(a));t.SetEnd(vec(z));t.SetLayer(p.F_Cu);t.SetWidth(150000);t.SetNet(board.FindNet(net));board.Add(t)
    if gap is not None:clearance_overrides[t.m_Uuid.AsString()]=gap
    return t

def via(board,at):
    t=p.PCB_VIA(board);t.SetPosition(vec(at));t.SetWidth(500000);t.SetDrill(200000);t.SetViaType(p.VIATYPE_THROUGH);t.SetLayerPair(p.F_Cu,p.B_Cu);t.SetNet(board.FindNet('signal'));board.Add(t);return t

def pad(board,at):
    f=p.FOOTPRINT(board);f.SetReference('T'+str(len(list(board.GetFootprints()))));board.Add(f)
    t=p.PAD(f);t.SetNumber('1');t.SetPosition(vec(at));t.SetSize(vec((300000,300000)));t.SetAttribute(p.PAD_ATTRIB_SMD);t.SetShape(p.PAD_SHAPE_RECT);ls=p.LSET();ls.AddLayer(p.F_Cu);t.SetLayerSet(ls);t.SetNet(board.FindNet('signal'));f.Add(t);return t

def fresh(board,ids=(),floor=300000):
    world=LocalRouter(board,deadline=stop,compact=True,track_width_nm=150000,ignored_copper=ids)
    world.minimum_clearance=max(world.minimum_clearance,floor)
    world.net_clearances={n:max(g,floor) for n,g in world.net_clearances.items()};return world

def state(world):
    return (tuple((id(table),tuple((cell,id(items),tuple((q[0],id(q[1]),*q[2:]) for q in items)) for cell,items in sorted(table.items()))) for table in [*world.buckets.values(),world.holes]),dict(world.net_clearances),dict(world.hole_clearances),world.minimum_clearance)

def query(world):
    shape=p.SHAPE_SEGMENT(vec((8000000,8000000)),vec((9000000,8000000)),150000)
    return ([world.via_clear(at,'signal') for at in ((5000000,5000000),(6000000,5000000),(10000000,10000000))],list(world.collisions(shape,p.F_Cu,'signal')))

case=sys.argv[1];out=Path(sys.argv[2]);report={}
assert callable(getattr(escape,'_physical_proof_world',None)), 'Completed-board proof reuse is not implemented'
if case=='integration':
    b=fixture();pads=[pad(b,(3000000,y)) for y in (4000000,12000000)];before=snapshot(b);ports={};plans={}
    for i,y in enumerate((4000000,12000000)):
        lead=track(b,(3000000,y),(5000000,y));v=via(b,(5000000,y));tail=track(b,(5000000,y),(7000000,y));tail.SetLayer(p.B_Cu)
        ids=[t.m_Uuid.AsString() for t in (lead,v,tail)]
        ports[str(i)]={'net':'signal','pad_ids':[pads[i].m_Uuid.AsString()],'added_ids':ids,'terminal_id':ids[-1],'exit_nm':(7000000,y),'heading_origin_nm':(5000000,y),'exit_layer':p.B_Cu,'kind':'via','via_id':ids[1],'via_nm':(5000000,y)}
        plans[str(i)]={'legs':[(p.F_Cu,[(3000000,y),(5000000,y)]),(p.B_Cu,[(5000000,y),(7000000,y)])],'vias':[(5000000,y)]}
    original=LocalRouter.__init__;calls=[]
    def counted(self,*a,**kw):calls.append(type(self).__name__);original(self,*a,**kw)
    LocalRouter.__init__=counted
    try:
        with reserve_launches([]):escape._prove(b,before,ports,plans,[],stop,173000)
    finally:LocalRouter.__init__=original
    assert len(calls)==2,calls
    report['constructors']=calls
elif case in ('stale-launch','fixed-launch'):
    b=fixture();a=pad(b,(4000000,4000000));pad(b,(14000000,14000000) if case=='stale-launch' else (6000000,4000000));t=track(b,(4000000,4000000),(6000000,4000000))
    port={'net':'signal','pad_ids':[a.m_Uuid.AsString()],'added_ids':[t.m_Uuid.AsString()],'terminal_id':t.m_Uuid.AsString(),'exit_nm':(6000000,4000000),'heading_origin_nm':(4000000,4000000),'exit_layer':p.F_Cu,'kind':'same_layer'}
    key=interface_identity(port);results=[]
    for cached in (False,True):
        with reserve_launches([port],resolved=[key] if case=='stale-launch' else []):
            graph=copper_graph(b,{'signal'},stop)
            world=escape._physical_proof_world(b,graph,{'signal'},stop,300000) if cached else fresh(b)
            if cached:assert isinstance(world,FrozenProofRouter)==(case=='fixed-launch'), 'Only stable launch scopes may be sealed'
            hits=list(world.collisions(p.SHAPE_SEGMENT(vec((4000000,4000000)),vec((6000000,4000000)),150000),p.F_Cu,'signal'))
            results.append((query(world),deepcopy(current_launch_inventory().resolved),hits))
            if case=='fixed-launch':
                assert hits==['controller-first-contact'],hits
                if cached:world.assert_unchanged()
            assert key not in current_launch_inventory().resolved
    assert results[0]==results[1],results
    report['parity']=True
else:
    b=fixture();owned=[via(b,(5000000,5000000)),via(b,(6000000,5000000)),track(b,(5000000,5000000),(6000000,5000000),gap=450000)]
    retained=via(b,(10000000,10000000));track(b,(8000000,8500000),(9000000,8500000),'foreign')
    if case=='same-net-contributor':
        contributor=pad(b,(15000000,15000000));contributor.SetLocalClearance(400000)
    invalid_pad=pad(b,(17000000,17000000)) if case=='invalid-nested' else None
    ids=[t.m_Uuid.AsString() for t in owned];before=snapshot(b)
    with reserve_launches([]):
        graph=copper_graph(b,{'signal','foreign'},stop)
        world=escape._physical_proof_world(b,graph,{'signal','foreign'},stop,300000)
        assert isinstance(world,FrozenProofRouter)
        indexed=state(world)
        if case in ('parity','same-net-contributor'):
            expected=fresh(b,ids)
            for order in (ids,list(reversed(ids))):
                with world.excluding_copper(order,clearance_nm=300000):
                    assert world.net_clearances==expected.net_clearances,(world.net_clearances,expected.net_clearances)
                    assert world.hole_clearances==expected.hole_clearances
                    actual=query(world);reference=query(expected)
                    assert actual==reference,(actual,reference)
                    assert actual[0]==[True,True,False],actual
                    assert bool(actual[1])==(case=='same-net-contributor'),actual
                    assert world.net_clearances['signal']==(401000 if case=='same-net-contributor' else 300000)
                assert state(world)==indexed
        elif case=='exception':
            try:
                with world.excluding_copper(ids,clearance_nm=300000):raise RuntimeError('injected')
            except RuntimeError:pass
            assert state(world)==indexed
        elif case=='invalid-nested':
            for excluded in (['missing'],[invalid_pad.m_Uuid.AsString()]):
                try:
                    with world.excluding_copper(excluded,clearance_nm=300000):raise AssertionError('Invalid exclusion accepted')
                except ValueError:pass
            with world.excluding_copper(ids,clearance_nm=300000):
                try:
                    with world.excluding_copper(ids,clearance_nm=300000):raise AssertionError('Nested exclusion accepted')
                except ValueError:pass
            assert state(world)==indexed
            world.assert_unchanged();assert snapshot(b)==before
            report['passed']=True;out.write_text(json.dumps(report));raise SystemExit(0)
        elif case=='seal':
            retained.SetPosition(vec((11000000,10000000)))
            try:world.assert_unchanged();raise AssertionError('Mutation accepted')
            except ValueError:pass
            report['passed']=True;out.write_text(json.dumps(report));raise SystemExit(0)
        elif case=='expiry':
            with world.excluding_copper(ids,clearance_nm=300000):world.deadline=time.monotonic()-1
            assert state(world)==indexed
            try:world.assert_unchanged();raise AssertionError('Expiry accepted')
            except TimeoutError:pass
            report['passed']=True;out.write_text(json.dumps(report));raise SystemExit(0)
        world.assert_unchanged();assert snapshot(b)==before
report['passed']=True;out.write_text(json.dumps(report))
