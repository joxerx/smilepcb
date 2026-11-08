"""Host control-flow checks only; native index/geometry safety has separate tests."""
import ast
from pathlib import Path
from types import ModuleType,SimpleNamespace
import sys
import pytest

SOURCE=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/field.py'


def exercise(monkeypatch,*,missing=3,deadline=100):
    from collections import Counter
    import math,statistics
    clock=SimpleNamespace(now=0.)
    created=[];calls=[];items=[]
    class Pad:
        def __init__(self,index):self.index=index
        def GetPosition(self):return SimpleNamespace(x=self.index*1000000,y=0)
        def IsOnLayer(self,layer):return missing==0
    pads=[Pad(i) for i in range(max(missing,2))]
    class Board:
        def GetDesignSettings(self):return SimpleNamespace(m_TrackMinWidth=150000)
        def GetTracks(self):return list(items)
        def Delete(self,item):items.remove(item)
    board=Board()
    class PlanningRouter:
        def __init__(self,board,*,deadline,**kwargs):self.board=board;self.deadline=deadline;self.track_width=150000
        def expired(self):return clock.now>=self.deadline
        def clear(self,*args):return True
        def add_path(self,*args):pass
    class BranchRouter:
        def __init__(self,board,*,deadline,**kwargs):
            self.board=board;self.deadline=deadline;self.added=[]
            created.append({'router':self,'time':clock.now,'deadline':deadline})
            clock.now+=.25
        def connect(self,pad,target):
            calls.append({'router':self,'time':clock.now,'deadline':self.deadline,
                          'last_search_present':hasattr(self,'last_search'),'last_plan_present':hasattr(self,'last_plan'),
                          'grid_step':self.grid_step,'connection_budget_s':self.connection_budget_s,
                          'search_budget_present':hasattr(self,'search_budget_s'),'heuristic_weight':self.heuristic_weight,
                          'pad':pad.index,'target':target.position,'added_before':len(self.added)})
            self.last_search={'old':pad.index};self.last_plan={'old':pad.index}
            uid=str(len(items));item=SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda uid=uid:uid))
            items.append(item);self.added.append(uid);clock.now+=.4
            return True
    matrix=ModuleType('smilepcb.keyboard_routing.matrix');matrix.KeyboardRouter=BranchRouter
    graph=ModuleType('smilepcb.keyboard_routing.native_graph')
    graph.Endpoint=lambda position,layer,net:SimpleNamespace(position=position,layer=layer,net=net)
    geometry=ModuleType('smilepcb.keyboard_routing.field_geometry');geometry.row_channels=lambda *args:[0]
    monkeypatch.setitem(sys.modules,matrix.__name__,matrix)
    monkeypatch.setitem(sys.modules,graph.__name__,graph)
    monkeypatch.setitem(sys.modules,geometry.__name__,geometry)
    node=next(q for q in ast.parse(SOURCE.read_text()).body if isinstance(q,ast.FunctionDef) and q.name=='_seed_straight_row')
    env={'__name__':'smilepcb.keyboard_routing.field','__package__':'smilepcb.keyboard_routing',
         'math':math,'statistics':statistics,'Counter':Counter,'time':SimpleNamespace(monotonic=lambda:clock.now),
         'p':SimpleNamespace(SHAPE_SEGMENT=lambda *args:object()),'vec':lambda q:q,'xy':lambda q:(q.x,q.y),
         'LocalRouter':PlanningRouter,'_row_obstacles':lambda *args:[],
         'axial_path':lambda *args:{'paths':[],'vias':[],'cost':0},'commit_plan':lambda *args:None}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),env)
    report=env['_seed_straight_row'](board,'arbitrary-row',pads,row_layer=2,domain=(-1000000,1000000),deadline=deadline)
    return report,created,calls


def test_no_missing_pads_builds_no_branch_router(monkeypatch):
    report,created,calls=exercise(monkeypatch,missing=0)
    assert report['complete'] and not created and not calls


def test_many_missing_pads_reuse_one_lazy_branch_router(monkeypatch):
    report,created,calls=exercise(monkeypatch,missing=4)
    assert report['complete'] and len(calls)==4
    assert len(created)==1
    assert all(q['router'] is created[0]['router'] for q in calls)
    assert [q['added_before'] for q in calls]==[0,1,2,3]


@pytest.mark.parametrize('deadline',[100,2.75])
def test_each_branch_deadline_keeps_three_second_and_shared_caps(monkeypatch,deadline):
    report,created,calls=exercise(monkeypatch,missing=3,deadline=deadline)
    assert report['complete'] and len(calls)==3
    assert all(0<q['deadline']-q['time']<=3 and q['deadline']<=deadline for q in calls)
    # The local allowance is refreshed per call, while the outer clock never moves.
    assert all(q['deadline']==min(deadline,q['time']+3) for q in calls[1:])


def test_reuse_does_not_leak_last_connection_diagnostics(monkeypatch):
    report,created,calls=exercise(monkeypatch,missing=3)
    assert report['complete']
    assert all(not q['last_search_present'] and not q['last_plan_present'] for q in calls)


def test_router_search_settings_preserve_direct_connect_behavior(monkeypatch):
    report,created,calls=exercise(monkeypatch,missing=3)
    assert report['complete']
    assert all((q['grid_step'],q['connection_budget_s'],q['heuristic_weight'])==(250000,2,3) for q in calls)
    # connection_budget_s is not read by direct connect; do not silently change
    # its existing default grid search budget while optimizing construction.
    assert all(not q['search_budget_present'] for q in calls)
