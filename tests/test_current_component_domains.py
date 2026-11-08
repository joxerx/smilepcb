"""Current graph scheduling proof; no native geometry feasibility claims."""
import ast,heapq,inspect,itertools,math,sys
from pathlib import Path
from types import SimpleNamespace as NS
import pytest


class Item:
    def __init__(self,uid):self.m_Uuid=NS(AsString=lambda:uid)


class Pad(Item):
    def GetNumber(self):return '1'


def scene(monkeypatch,*,deadline=20.,fail_first_tree=False):
    path=Path(__file__).parents[1]/'smilepcb/keyboard_routing/native_graph.py'
    fn=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='route_components')
    clock=NS(now=0.);clock.monotonic=lambda:clock.now
    state=NS(tree_joins=0,tree_attempts=0);calls=[];reads=[]
    def node(uid,net,point,pad=True):
        return dict(item=(Pad if pad else Item)(uid),net=net,points=[point],shapes={0:point})
    base=[node('a','tree',(0,0)),node('b','tree',(10,0)),node('c','tree',(30,0)),node('d','tree',(50,0)),
          node('x','slow',(0,100)),node('y','slow',(10,100))]
    def graph(*args):
        clock.now+=.1;reads.append((clock.now,args[-1]))
        nodes=list(base);parent=list(range(len(nodes)))
        if state.tree_joins:
            root=1 if fail_first_tree and state.tree_joins==1 else 0
            nodes.append(node('fresh-branch','tree',(20,10),False));parent.append(root)
            if root:parent[2]=1
            else:parent[1]=0
        if state.tree_joins>1:parent[1]=parent[2]=parent[3]=0  # Actual incidental merge, not merely the selected pair.
        def find(i):
            while parent[i]!=i:i=parent[i]
            return i
        def join(a,b):parent[find(a)]=find(b)
        groups={}
        for i in range(len(nodes)):groups.setdefault(find(i),[]).append(i)
        return dict(nodes=nodes,groups=groups,find=find,join=join)
    router=NS(deadline=deadline,port_policy=None,launch_policy=None,grid_step=250000,connection_budget_s=2,
        attempt_counts={},refresh_interfaces=lambda graph:None)
    router.expired=lambda:clock.now>=router.deadline
    def search(candidate,starts,targets,net):
        calls.append(dict(net=net,starts=list(starts),targets=list(targets),deadline=candidate.deadline,started=clock.now))
        if net=='slow':clock.now=candidate.deadline;candidate.last_search={'reason':'search_budget'};return False
        state.tree_attempts+=1
        if fail_first_tree and state.tree_attempts==1:
            clock.now=candidate.deadline;candidate.last_search={'reason':'search_budget'};return False
        clock.now+=.2;state.tree_joins+=1;candidate.last_search={'reason':'connected'};return True
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.grid_search',NS(search_many=search))
    ns=dict(__package__='smilepcb.keyboard_routing',p=NS(PAD=Pad),time=clock,itertools=itertools,heapq=heapq,
        distance=math.dist,copper_graph=graph,routing_points=lambda n:n['points'],nearest_to_node=lambda n,q:n['points'][0])
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),ns)
    def run(**kw):
        # Exercise actual old behavior in RED, rather than only a missing keyword.
        if 'refresh_components' not in inspect.signature(ns['route_components']).parameters:kw.pop('refresh_components',None)
        return ns['route_components'](None,router,{'tree','slow'},required_pad_ids={'a','b','c','d','x','y'},**kw)
    return NS(run=run,
        calls=calls,reads=reads,router=router,clock=clock,state=state)


def test_refresh_uses_new_copper_and_whole_merged_component_skips_incidental_merges(monkeypatch):
    s=scene(monkeypatch);assert s.run(refresh_components=True)==2
    tree=[c for c in s.calls if c['net']=='tree']
    assert len(tree)==2 and len(s.reads)==3
    endpoints=set(tree[1]['starts']+tree[1]['targets'])
    assert {((0,0),0),((10,0),0),((20,10),0)}<=endpoints
    assert len([c for c in s.calls if c['net']=='slow'])==1
    assert s.router.graph_diagnostic['attempt_counts']=={'slow':1,'tree':2}
    assert len(s.router.graph_diagnostic['attempts'])==3
    assert [c['net'] for c in s.calls]==['tree','slow','tree']
    assert s.router.graph_diagnostic['skipped_unchanged_domains']>=1
    assert all(c['deadline']<=min(20,c['started']+2) for c in s.calls)
    assert all(end==20 for _,end in s.reads) and s.router.deadline==20


def test_graph_refresh_charges_original_deadline_and_never_starts_expired_search(monkeypatch):
    s=scene(monkeypatch,deadline=.05)
    assert s.run(refresh_components=True)==0
    assert not s.calls and len(s.reads)==1 and s.router.deadline==.05


def test_legacy_default_retains_one_graph_pass(monkeypatch):
    s=scene(monkeypatch);s.run()
    assert len(s.reads)==1


def test_a_failed_pair_can_use_an_enlarged_current_component_domain_once(monkeypatch):
    s=scene(monkeypatch,fail_first_tree=True);assert s.run(refresh_components=True)==2
    tree=[c for c in s.calls if c['net']=='tree']
    assert len(tree)==3 and s.state.tree_attempts==3
    assert {((10,0),0),((30,0),0),((20,10),0)}<=set(tree[-1]['starts']+tree[-1]['targets'])
    history=s.router.graph_diagnostic['attempts']
    assert [a['connected'] for a in history if a['net']=='tree']==[False,True,True]
    assert len([a for a in history if a['net']=='slow'])==1
