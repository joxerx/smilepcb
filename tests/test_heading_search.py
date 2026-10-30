"""Run the real bounded search on a deterministic collision graph."""
import importlib.util
import math
from pathlib import Path
import sys
from types import SimpleNamespace

from smilepcb.routing_quality import current_quality,path_cost,using_quality_profile


def engine(monkeypatch):
    native=SimpleNamespace(F_Cu=0,B_Cu=2,FromMM=lambda mm:round(mm*1000000),
                           SHAPE_SEGMENT=lambda a,b,width:(a,b,width))
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',SimpleNamespace(p=native))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.local_router',
                        SimpleNamespace(vec=lambda x:x,distance=math.dist))
    path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/grid_search.py'
    spec=importlib.util.spec_from_file_location('smilepcb.keyboard_routing._grid_probe',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class GraphRouter:
    deadline=None;drill=200000;via_width=500000;track_width=150000
    grid_step=1000000;search_budget_s=5;search_expansion_limit=5000
    def __init__(self,edges):
        self.edges=edges;self.added=[];self.legs=[];self.quality=current_quality()
    def clear(self,shape,layer,net):
        a,b,_=shape
        def contains(x,y,q):
            return ((q[0]-x[0])*(y[1]-x[1])==(q[1]-x[1])*(y[0]-x[0]) and
                    all(min(x[i],y[i])<=q[i]<=max(x[i],y[i]) for i in (0,1)))
        return any(layer==face and contains(x,y,a) and contains(x,y,b) for x,y,face in self.edges)
    def via_clear(self,*_):return False
    def expired(self):return False
    def add_path(self,points,layer,net):
        self.legs.append((layer,points));self.added.append(len(self.added))
    def add_via(self,*_):raise AssertionError('No via is legal on this graph')


def test_search_trades_short_zigzag_for_fewer_bends_and_keeps_legacy(monkeypatch):
    grid=engine(monkeypatch)
    scale=lambda pt:tuple(v*1000000 for v in pt)
    zig=list(map(scale,[(0,0),(1,1),(2,0),(3,1),(4,0),(5,1),(6,0)]))
    smooth=list(map(scale,[(0,0),(0,-2),(6,-2),(6,0)]))
    edges=[(a,b,0) for line in (zig,smooth) for a,b in zip(line,line[1:])]
    results={}
    for mode in ('legacy','turns'):
        with using_quality_profile(mode):
            router=GraphRouter(edges)
            assert grid.search(router,(0,0),(6000000,0),[0],[0],'signal'),router.last_search
            results[mode]=router.legs[0][1]
            assert router.last_search['expanded']<=router.search_expansion_limit
            assert all(router.clear((a,b,150000),face,'signal') for face,pts in router.legs for a,b in zip(pts,pts[1:]))
    with using_quality_profile('turns'):
        assert path_cost(results['turns'])<path_cost(results['legacy'])
    assert len(results['turns'])<len(results['legacy'])


def test_expansion_limit_rejects_without_leaving_partial_copper(monkeypatch):
    grid=engine(monkeypatch)
    with using_quality_profile('turns'):
        router=GraphRouter([((0,0),(100000000,0),0)]);router.search_expansion_limit=1
        assert not grid.search(router,(0,0),(100000000,0),[0],[0],'signal')
        assert router.added==[]
        assert router.last_search['reason']=='expansion_budget'


def test_final_access_leads_keep_the_straight_target_available(monkeypatch):
    grid=engine(monkeypatch)
    with using_quality_profile('turns'):
        router=GraphRouter([((0,0),(7000000,0),0),((6000000,0),(6000000,1000000),0)])
        router.grid_step=125000
        assert grid.search_many(router,[((0,0),0)],[((6000000,1000000),0),((7000000,0),0)],'signal')
        assert router.legs[-1][1][-1]==(7000000,0)


def test_search_can_turn_after_a_via_without_carrying_the_old_heading(monkeypatch):
    grid=engine(monkeypatch)
    with using_quality_profile('turns'):
        router=GraphRouter([((0,0),(2000000,0),0),((2000000,0),(2000000,4000000),2)])
        router.via_clear=lambda point,net:point==(2000000,0)
        vias=[]
        router.add_via=lambda point,net:(vias.append(point),router.added.append('via'))
        assert grid.search(router,(0,0),(2000000,4000000),[0],[2],'signal'),router.last_search
        assert vias==[(2000000,0)]
        assert [face for face,_ in router.legs]==[0,2]
        assert sum(path_cost(points) for _,points in router.legs)==6000000


def test_dsn_grid_terminal_leads_are_exact_octile_paths_in_both_quality_modes(monkeypatch):
    grid=engine(monkeypatch)
    a,z=(103,207),(3000106,2000209)
    for mode in ('legacy','turns'):
        with using_quality_profile(mode):
            router=GraphRouter([]);router.clear=lambda *args:True
            router.generation=SimpleNamespace(name='dsn-safe-v1')
            assert grid.search(router,a,z,[0],[0],'signal'),router.last_search
            assert router.legs[0][1][0]==a and router.legs[-1][1][-1]==z
            assert all(not dx or not dy or abs(dx)==abs(dy) for _,points in router.legs
                       for x,y in zip(points,points[1:]) for dx,dy in [(y[0]-x[0],y[1]-x[1])])


def test_dsn_terminal_reconstruction_cannot_cut_across_a_blocked_octile_lead(monkeypatch):
    grid=engine(monkeypatch)
    a,z=(103,207),(3000106,2000209)
    router=GraphRouter([(a,z,0)]);router.generation=SimpleNamespace(name='dsn-safe-v1')
    assert not grid.search(router,a,z,[0],[0],'signal')
    assert router.added==[]
