"""Exact request-local heuristic work reduction; no native imports/timing claims."""
import ast
from collections import Counter
import math
from pathlib import Path
from types import ModuleType
import pytest
from test_heading_search import engine,GraphRouter
from smilepcb.routing_quality import using_quality_profile,path_cost


SOURCE=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/grid_search.py'


def heuristic_factory(grid):
    """Keep the production prefix/closure and return its actual nested estimate."""
    tree=ast.parse(SOURCE.read_text());search=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_search')
    index=next(i for i,n in enumerate(search.body) if isinstance(n,ast.FunctionDef) and n.name=='heuristic')
    search.body=search.body[:index+1]+[ast.Return(value=ast.Name(id='heuristic',ctx=ast.Load()))]
    namespace=dict(grid.__dict__);exec(compile(ast.fix_missing_locations(ast.Module(body=[search],type_ignores=[])),str(SOURCE),'exec'),namespace)
    return namespace['_search']


def uncached_reference():
    """Same engine, with only the pre-existing exact distance expression."""
    tree=ast.parse(SOURCE.read_text())
    class Reference(ast.NodeTransformer):
        def visit_FunctionDef(self,node):
            node=self.generic_visit(node)
            if node.name=='heuristic':node.body=ast.parse('return min(distance(pos(state),b)+(0 if state[2]==layer else layer_estimate) for b,layer in targets)').body
            return node
    module=ModuleType('smilepcb.keyboard_routing._uncached_heuristic_reference');module.__package__='smilepcb.keyboard_routing'
    exec(compile(ast.fix_missing_locations(Reference().visit(tree)),str(SOURCE),'exec'),module.__dict__)
    return module


def test_repeated_heading_and_via_history_labels_share_one_spatial_layer_estimate(monkeypatch):
    grid=engine(monkeypatch);calls=[]
    def distance(a,b):calls.append((a,b));return math.dist(a,b)
    grid.distance=distance;factory=heuristic_factory(grid);router=GraphRouter([]);router.layer_change_heuristic_nm=500000
    targets=[((5000000,0),0),((0,5000000),2)]
    estimate=factory(router,[((0,0),0)],targets,'signal')
    # enqueue invokes heuristic for distinct heading/history labels although
    # distance to the fixed targets depends only on x/y/layer.
    labels=[((1,1,0,(1,0)),frozenset()),((1,1,0,(0,1)),frozenset()),
            ((1,1,0,(1,0)),frozenset({(2000000,0)}))]
    values=[estimate(state) for state,history in labels]
    expected=min(math.dist((1000000,1000000),point)+(0 if 0==layer else 500000) for point,layer in targets)
    assert values==[expected]*3
    assert calls==[((1000000,1000000),point) for point,layer in targets],calls
    # The other copper face has a different estimate and exactly one new
    # evaluation in the same original target order.
    face=estimate((1,1,2,(1,0)));assert face==min(math.dist((1000000,1000000),point)+(0 if 2==layer else 500000) for point,layer in targets)
    assert len(calls)==4


def test_zero_estimate_is_cached_and_target_sets_are_fresh_per_search(monkeypatch):
    grid=engine(monkeypatch);calls=[]
    def distance(a,b):calls.append((a,b));return math.dist(a,b)
    grid.distance=distance;factory=heuristic_factory(grid);router=GraphRouter([])
    here=((0,0),0);first=factory(router,[here],[here],'signal')
    assert first((0,0,0,(1,0)))==first((0,0,0,(0,1)))==0
    assert len(calls)==1,calls
    other=factory(router,[here],[((2000000,0),0)],'signal')
    assert other((0,0,0,(1,0)))==2000000 and len(calls)==2,calls
    assert first((0,0,0,(0,-1)))==0 and len(calls)==2,calls


@pytest.mark.parametrize('quality',['legacy','turns'])
def test_real_graph_search_keeps_path_score_and_state_domain_with_fewer_distance_calls(monkeypatch,quality):
    grid=engine(monkeypatch);reference=uncached_reference()
    scale=lambda q:tuple(v*1000000 for v in q)
    zig=list(map(scale,[(0,0),(1,1),(2,0),(3,1),(4,0),(5,1),(6,0)]))
    smooth=list(map(scale,[(0,0),(0,-2),(6,-2),(6,0)]))
    edges=[(a,b,0) for line in (zig,smooth) for a,b in zip(line,line[1:])]
    results=[]
    with using_quality_profile(quality):
        for module in (reference,grid):
            calls=Counter()
            def distance(a,b):calls['distance']+=1;return math.dist(a,b)
            module.distance=distance;router=GraphRouter(edges)
            assert module.search(router,(0,0),(6000000,0),[0],[0],'signal'),router.last_search
            results.append({'legs':router.legs,'score':sum(path_cost(points) for face,points in router.legs),
                            'expanded':router.last_search['expanded'],'states':router.last_search['states'],'calls':calls['distance']})
    assert {k:v for k,v in results[0].items() if k!='calls'}=={k:v for k,v in results[1].items() if k!='calls'},results
    assert results[1]['calls']<=results[0]['calls'],results
    if quality=='turns':assert results[1]['calls']<results[0]['calls'],results


def test_real_via_graph_keeps_faces_vias_and_layer_estimates(monkeypatch):
    grid=engine(monkeypatch);reference=uncached_reference();results=[]
    with using_quality_profile('turns'):
        for module in (reference,grid):
            router=GraphRouter([((0,0),(2000000,0),0),((2000000,0),(2000000,4000000),2)])
            router.via_clear=lambda point,net:point==(2000000,0);vias=[]
            router.add_via=lambda point,net:(vias.append(point),router.added.append('via'))
            assert module.search(router,(0,0),(2000000,4000000),[0],[2],'signal'),router.last_search
            results.append((router.legs,vias,router.last_search['expanded'],router.last_search['states']))
    assert results[0]==results[1]
