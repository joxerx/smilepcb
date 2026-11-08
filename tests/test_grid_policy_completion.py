"""Real grid search on a small, deterministic geometric collision graph."""
import importlib.util
import math,os,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from smilepcb.routing_quality import current_quality,using_quality_profile

def engine(monkeypatch):
    native=SimpleNamespace(F_Cu=0,B_Cu=2,FromMM=lambda mm:round(mm*1000000),
                           SHAPE_SEGMENT=lambda a,b,width:(a,b,width))
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',SimpleNamespace(p=native))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.local_router',SimpleNamespace(vec=lambda x:x,distance=math.dist))
    source=Path(os.environ.get('SMILEPCB_GRID_SEARCH_SOURCE',str(Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/grid_search.py')))
    spec=importlib.util.spec_from_file_location('smilepcb.keyboard_routing._policy_regression',source)
    grid=importlib.util.module_from_spec(spec);spec.loader.exec_module(grid)
    return grid

class Router:
    deadline=None;drill=200000;via_width=500000;track_width=150000
    grid_step=1000000;search_budget_s=3;search_expansion_limit=20000
    def __init__(self,edges,policy):
        self.edges=edges;self.path_policy=policy;self.added=[];self.legs=[];self.quality=current_quality()
    def clear(self,shape,layer,net):
        a,b,_=shape
        def contains(x,y,q):
            return ((q[0]-x[0])*(y[1]-x[1])==(q[1]-x[1])*(y[0]-x[0]) and
                    all(min(x[i],y[i])<=q[i]<=max(x[i],y[i]) for i in (0,1)))
        return any(layer==face and contains(x,y,a) and contains(x,y,b) for x,y,face in self.edges)
    def via_clear(self,*args):return False
    def expired(self):return False
    def add_path(self,path,face,net):self.legs.append((face,path));self.added.append(len(self.added))
    def add_via(self,*args):raise AssertionError('No via in this collision graph')

class Angles:
    def accepts_path(self,path):
        directions=[(b[0]-a[0],b[1]-a[1]) for a,b in zip(path,path[1:]) if a!=b]
        for a,b in zip(directions,directions[1:]):
            dot=sum(x*y for x,y in zip(a,b))
            if dot<=0 or 2*dot*dot<sum(x*x for x in a)*sum(x*x for x in b):return False
        return True

@pytest.mark.parametrize('quality',['legacy','turns'])
def test_legal_smooth_detour_survives_shorter_invalid_right_angle(monkeypatch,quality):
    grid=engine(monkeypatch);scale=lambda pt:tuple(v*1000000 for v in pt)
    short=list(map(scale,[(0,0),(6,0),(6,4)]))
    smooth=list(map(scale,[(0,0),(0,-3),(3,-6),(9,-6),(12,-3),(12,1),(9,4),(6,4)]))
    edges=[(a,b,0) for line in (short,smooth) for a,b in zip(line,line[1:])]
    with using_quality_profile(quality):
        router=Router(edges,Angles())
        assert grid.search(router,short[0],short[-1],[0],[0],'signal'),router.last_search
        assert router.legs[0][1][0]==short[0] and router.legs[-1][1][-1]==short[-1]
        assert all(router.path_policy.accepts_path(path) for _,path in router.legs)
        assert all(router.clear((a,b,router.track_width),face,'signal') for face,path in router.legs for a,b in zip(path,path[1:]))

def test_rejected_full_plan_does_not_hide_a_valid_second_terminal(monkeypatch):
    grid=engine(monkeypatch)
    class FinalContract:
        def accepts_path(self,path):
            return not(path[0]==(0,0) and path[-1][1]==0 and path[-1][0]>=5000000)
    with using_quality_profile('turns'):
        router=Router([((0,0),(6000000,0),0),((0,0),(0,7000000),0)],FinalContract())
        assert grid.search_many(router,[((0,0),0)],[((6000000,0),0),((0,7000000),0)],'signal'),router.last_search
        assert router.legs[-1][1][-1]==(0,7000000)
