"""Negotiation feedback must use real post-route interface closure, never stale launches."""
import ast
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import pytest


def case(monkeypatch, *, closed=True, physical_blocker=False, has_policy=True, repair_reserved=False):
    class Track:
        def __init__(self, uid, net):
            self.uid=uid;self.net=net;self.m_Uuid=SimpleNamespace(AsString=lambda:uid)
        def GetNetname(self):return self.net
        def GetClass(self):return 'PCB_TRACK'
        def IsLocked(self):return False
        def IsOnLayer(self, face):return face==0
        def GetEffectiveShape(self, face):return object()
    class Via(Track):pass
    fixed=Track('fixed','control');board=SimpleNamespace(items={'fixed':fixed})
    board.GetTracks=lambda:list(board.items.values())
    board.Delete=lambda item:board.items.pop(item.uid)
    board.Add=lambda item:board.items.__setitem__(item.uid,item)
    calls={'graphs':[],'refresh':[]};routers=[];native_graph=object()
    class Router:
        def __init__(self, candidate, **kwargs):
            self.added=[];self.graph_diagnostic={};self.complete=False
            self.port_policy=SimpleNamespace(ports=[{'net':'target'},{'net':'peer'},{'net':'aux'}],repair_reserved=repair_reserved) if has_policy else None
            routers.append(self)
        def collisions(self, *args):
            if self.port_policy is not None and (not self.complete or self.port_policy.repair_reserved):yield 'reserved-controller-launch'
            if physical_blocker:yield 'fixed'
        def refresh_interfaces(self, graph):
            assert graph is native_graph
            calls['refresh'].append(self);self.complete=closed
    def graph(candidate,nets,deadline):
        assert has_policy,'Unscoped negotiation should not add interface graph work'
        calls['graphs'].append((candidate,set(nets),deadline));return native_graph
    def route(candidate,router,nets,**kwargs):
        item=Track('candidate',next(iter(nets)));candidate.Add(item);router.added.append(item.uid);return 1
    local=ModuleType('smilepcb.keyboard_routing.local_router');local.LocalRouter=Router
    carve=ModuleType('smilepcb.keyboard_routing.carve');carve.retain_clear_fragments=lambda *args:[]
    monkeypatch.setitem(sys.modules,local.__name__,local);monkeypatch.setitem(sys.modules,carve.__name__,carve)
    path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/native_graph.py'
    node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='negotiate_routes')
    env={'__package__':'smilepcb.keyboard_routing','time':SimpleNamespace(monotonic=lambda:1000.),
         'p':SimpleNamespace(F_Cu=0,B_Cu=2,PCB_VIA=Via,FromMM=lambda v:round(v*1000000)),
         'route_components':route,'copper_graph':graph}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),env)
    return env['negotiate_routes'],board,Router,calls,routers


def test_feedback_retires_only_actual_closed_launches_after_routing(monkeypatch):
    run,board,factory,calls,routers=case(monkeypatch)
    result=run(board,{'target'},{'fixed'},1100.,router_factory=factory)
    assert result['connected']==1 and result['removed']==[]
    assert set(board.items)=={'fixed','candidate'}
    assert calls['graphs']==[(board,{'target','peer','aux'},1100.)]
    assert calls['refresh']==[routers[0]]


def test_actual_open_peer_launch_is_still_a_hard_obstacle(monkeypatch):
    run,board,factory,calls,_=case(monkeypatch,closed=False)
    with pytest.raises(ValueError,match='reserved-controller-launch'):
        run(board,{'target'},{'fixed'},1100.,router_factory=factory)
    assert set(board.items)=={'fixed'}
    assert calls['graphs']==[(board,{'target','peer','aux'},1100.)]


def test_refresh_does_not_waive_real_protected_copper(monkeypatch):
    run,board,factory,calls,_=case(monkeypatch,physical_blocker=True)
    with pytest.raises(ValueError,match='fixed'):
        run(board,{'target'},{'fixed'},1100.,router_factory=factory)
    assert set(board.items)=={'fixed'} and calls['refresh']


def test_unscoped_negotiation_keeps_existing_no_graph_path(monkeypatch):
    run,board,factory,calls,_=case(monkeypatch,has_policy=False)
    assert run(board,{'target'},{'fixed'},1100.,router_factory=factory)['connected']==1
    assert calls['graphs']==[] and calls['refresh']==[]


def test_explicit_repair_reservation_survives_actual_closure_refresh(monkeypatch):
    run,board,factory,calls,_=case(monkeypatch,repair_reserved=True)
    with pytest.raises(ValueError,match='reserved-controller-launch'):
        run(board,{'target'},{'fixed'},1100.,router_factory=factory)
    assert set(board.items)=={'fixed'} and calls['refresh']
