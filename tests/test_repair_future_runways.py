"""Repair-only reservation semantics; geometry itself is tested natively."""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys,time
import pytest

from smilepcb.keyboard_routing.interface_launches import reserve_launches,current_launch_inventory,interface_identity
from test_dormant_source_policy import scene,Shape
from test_post_matrix_interface_scope import local_router_type
from test_complex_repair_source_preservation import scene as wrapper_scene

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('operation,want',[
    ('complete',{('pad-a',),('pad-b',)}),('completion_candidates',{('pad-a',),('pad-b',)}),
    ('components',set()),('cleanup',set()),
])
def test_only_complex_repair_reserves_intact_sources_without_reopening_them(monkeypatch,tmp_path,operation,want):
    from smilepcb.keyboard_routing import interface_launches
    observed=[];original=interface_launches.current_launches
    def inspect():
        inventory=current_launch_inventory()
        observed.append((set(getattr(inventory,'repair_reserved',())),set(inventory.resolved)))
        return original()
    monkeypatch.setattr(interface_launches,'current_launches',inspect)
    run,state,before,ports,checks=wrapper_scene(monkeypatch,tmp_path,operation=operation)
    run()
    assert observed==[(want,{('pad-b',),('pad-c',)})]
    assert current_launch_inventory() is None and state==before


def test_nested_scope_keeps_reservation_identity_but_leaks_nothing_on_exit(scene):
    board,port,Policy=scene;key=interface_identity(port)
    with reserve_launches([port],resolved=[key],repair_reserved=[list(key)]):
        assert current_launch_inventory().resolved=={key}
        with reserve_launches([port],resolved=[key]):assert current_launch_inventory().repair_reserved=={key}
        with reserve_launches([],resolved=[]):assert current_launch_inventory().repair_reserved==set()
        assert current_launch_inventory().repair_reserved=={key}
    with reserve_launches([port],resolved=[key]):assert current_launch_inventory().repair_reserved==set()
    assert current_launch_inventory() is None


def reserved_policy(scene):
    board,port,Policy=scene;key=interface_identity(port)
    return board,port,Policy(board,[port],resolved=[key],repair_reserved=[key]),Policy


def test_closed_reserved_source_keeps_true_closure_and_field_contacts(scene):
    board,port,policy,Policy=reserved_policy(scene);key=interface_identity(port)
    assert policy.resolved=={key} and policy.active==set() and policy.live_ports==[] and policy.by_net=={}
    graph=board.graph(True)
    for node in graph['nodes']:
        item=node['item'];point=tuple(item.position) if hasattr(item,'position') else item.a
        node.update(zone=False,shapes={0:Shape()},points=[point])
    policy.contacts.__globals__['routing_points']=lambda node:node['points']
    policy.contacts.__globals__['nearest_to_node']=lambda node,toward:node['points'][0]
    assert ((0,0),0) in policy.contacts(graph,list(range(len(graph['nodes']))),(1,0))
    assert policy.accepts_segment((200000,0),(400000,0),0,'control',shape=Shape(hits={'head','capsule'}))
    policy.refresh(board.graph(True))
    assert policy.resolved=={key} and policy.live_ports==[]


def test_foreign_reservation_only_uses_its_real_face_and_catches_through_vias(scene,monkeypatch):
    board,port,policy,Policy=reserved_policy(scene)
    native=sys.modules['smilepcb.native_worker'].p
    native.SHAPE_CIRCLE=lambda *args:Shape(hits={'capsule'})
    foreign=Shape(hits={'capsule'})
    assert policy._launch_conflict(foreign,0,'foreign')
    assert not policy._launch_conflict(foreign,2,'foreign')
    assert not policy._launch_conflict(foreign,0,'control')
    assert not policy.accepts_via((1200000,0),'foreign',500000)
    assert policy.accepts_via((1200000,0),'control',500000)


def test_scoped_policy_override_preserves_future_reservation_and_capacity_exclusion(scene):
    board,port,captured,Policy=reserved_policy(scene);key=interface_identity(port)
    router=object.__new__(local_router_type());router.board=board;router.track_width=150000
    router._port_policy=None;router.launch_policy=captured;router._captured_launch_policy=captured
    with reserve_launches([port],resolved=[key],repair_reserved=[key]):
        router.launch_inventory=current_launch_inventory()
        replacement=Policy(board,[port],resolved=[key])
        router.port_policy=replacement
        assert router.port_policy._launch_conflict(Shape(hits={'capsule'}),0,'foreign')
        assert router.port_policy.active==set() and router.port_policy.resolved=={key}
        router.launch_policy=None
        router.port_policy=Policy(board,[],resolved=[])
        assert not router.port_policy._launch_conflict(Shape(hits={'capsule'}),0,'foreign')


@pytest.mark.parametrize('kind',['track','via'])
def test_backend_delta_checks_foreign_reserved_strip_even_when_source_stays_closed(scene,monkeypatch,kind):
    board,port,policy,Policy=reserved_policy(scene);key=interface_identity(port)
    native=sys.modules['smilepcb.native_worker'].p
    native.SHAPE_SEGMENT=lambda *args:Shape(hits={'head','capsule'})
    native.SHAPE_CIRCLE=lambda *args:Shape(hits={'capsule'})
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.port_policy',SimpleNamespace(PortPolicy=Policy))
    if kind=='via':
        class Item(native.PCB_VIA):
            m_Uuid=SimpleNamespace(AsString=lambda:'new')
            def GetNetname(self):return 'foreign'
            def GetPosition(self):return SimpleNamespace(x=1200000,y=0)
            def IsOnLayer(self,layer):return layer in (0,2)
            def GetWidth(self,layer):return 500000
        shape={'class':'PCB_VIA','start':[1200000,0],'end':[1200000,0],'net':'foreign','width':500000,'layer':0}
    else:
        class Item:
            m_Uuid=SimpleNamespace(AsString=lambda:'new')
            def GetNetname(self):return 'foreign'
        shape={'class':'PCB_TRACK','start':[1200000,-1000000],'end':[1200000,1000000],'net':'foreign','width':150000,'layer':0}
    before=deepcopy(board.state());after=deepcopy(before);after['tracks']['new']=shape
    source=ROOT/'smilepcb/keyboard_native.py';node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_admit_interface_delta')
    env={'p':native};exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
    with pytest.raises(ValueError,match='first-contact'):
        env['_admit_interface_delta'](board,SimpleNamespace(GetTracks=lambda:[Item()]),before,after,[port],[key],repair_reserved=[key],deadline=time.monotonic()+1)
