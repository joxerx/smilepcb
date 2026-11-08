"""Host-only goal scheduling/inventory; native copper is checked separately."""
import ast
from collections import Counter,defaultdict
from copy import deepcopy
from dataclasses import dataclass
import heapq,math,time
from pathlib import Path
from types import SimpleNamespace


def policy():
    """Load pure definitions without importing pcbnew or starting native work."""
    root=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing'
    ns={'__name__':__name__,'math':math,'time':time,'heapq':heapq,'Counter':Counter,
        'defaultdict':defaultdict,'deepcopy':deepcopy,'dataclass':dataclass,
        'p':SimpleNamespace(F_Cu=0,B_Cu=2)}
    for filename in ('collector_capacity.py','collector_peel.py'):
        tree=ast.parse((root/filename).read_text())
        definitions=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='DIRS' for t in n.targets)]
        class DefinitionsOnly(ast.NodeTransformer):
            def visit_ImportFrom(self,node):return None
        module=DefinitionsOnly().visit(ast.Module(body=definitions,type_ignores=[]))
        exec(compile(ast.fix_missing_locations(module),str(root/filename),'exec'),ns)
    return ns


def test_first_opportunities_share_the_original_deadline_and_state_pool():
    ns=policy();assert 'GoalSchedule' in ns,'Alternative contacts need a shared bounded goal agenda'
    agenda=ns['GoalSchedule'](3,deadline=8)
    a=agenda.next_turn(now=0,expanded=0,rejections=0)
    b=agenda.next_turn(now=1,expanded=100,rejections=1)
    c=agenda.next_turn(now=2,expanded=200,rejections=2)
    assert [a[0],b[0],c[0]]==[0,1,2]
    assert a[1]==8/3 and b[1]==4.5 and c[1]==8
    assert a[2]==6000 and b[2]==9050 and c[2]==18000
    assert agenda.next_turn(now=8,expanded=200,rejections=2) is None
    assert agenda.next_turn(now=3,expanded=18000,rejections=2) is None
    assert agenda.next_turn(now=3,expanded=200,rejections=12) is None


def test_goals_get_one_opportunity_before_nearest_retries():
    ns=policy();assert 'GoalSchedule' in ns,'A failed nearest contact currently monopolizes native retries'
    agenda=ns['GoalSchedule'](17,deadline=8)
    turns=[agenda.next_turn(now=i/100,expanded=i,rejections=0)[0] for i in range(18)]
    assert turns==list(range(17))+[0]
    assert agenda.count==17


class Item:
    def __init__(self,uid,layers,position=(10000000,1000000)):
        self.m_Uuid=SimpleNamespace(AsString=lambda:uid);self.layers=layers;self.position=position
    def GetClass(self):return 'PAD'
    def GetPosition(self):return SimpleNamespace(x=self.position[0],y=self.position[1])
    def GetSize(self):return SimpleNamespace(x=300000,y=150000)
    def GetDrillSize(self):return SimpleNamespace(x=300000 if len(self.layers)>1 else 0,y=300000 if len(self.layers)>1 else 0)
    def IsOnLayer(self,layer):return layer in self.layers
    def GetParentFootprint(self):return SimpleNamespace(GetReference=lambda:'field')
    def GetNumber(self):return '1'


def test_duplicate_contacts_keep_real_face_witnesses_without_extra_goal_turns():
    ns=policy();assert 'branch_goals' in ns,'Only the nearest real field witness is currently returned'
    ports=[{'net':'signal','exit_nm':[0,0],'heading_origin_nm':[-1000000,0],'collector_heading':[1,0]}]
    f=Item('f-only',{0});through=Item('plated',{0,2});far=Item('far',{0},(20000000,1000000))
    targets={'signal':[(i,{'item':i,'points':[i.position],'zone':False}) for i in (f,through,far)]}
    goals=ns['branch_goals'](None,None,ports,['signal'],targets)
    assert len(goals)==2
    assert goals[0].point==(10000000,1000000)
    assert {w.item.m_Uuid.AsString() for w in goals[0].witnesses}=={'f-only','plated'}
    assert not f.IsOnLayer(2) and through.IsOnLayer(2)
    assert goals[1].point==(20000000,1000000)


def synthetic_search():
    ns=policy();base=ns['CollectorSearch']
    class SearchGraph(base):
        """An unobstructed cardinal graph; copper admission is native-test work."""
        def __init__(self):
            self.h=(1,0);self.face=0;self.heading_index=0;self.origin=(0,0);self.offsets={'signal':0}
            self.lookup={'signal':{'exit_nm':(0,0)}};self.nets=['signal'];self.ports=[]
            self.step=500000;self.width=150000;self.radius=75000;self.gap=150000;self.reserved_width=450000
            self.deadline=time.monotonic()+2;self.allow_vias=False;self.run_cap=1
            self.cache={};self.portal_cache={};self.blockers=Counter();self.best_partial=None;self.best_partial_score=-math.inf
            self.stats={'expanded':0,'generated':0,'native_goal_rejections':0,'portal_proposals':0,'portal_legal':0}
            self.branch=None;self.interface_graph=None;self.calls=[]
            targets=[ns['FieldTarget'](None,None,(2000000,0)),ns['FieldTarget'](None,None,(14000000,0))]
            # Attributes follow the actual constructor, with native world creation
            # replaced by this small graph fixture.
            self.goals=[SimpleNamespace(leaf_net='signal',heading=(1,0),side=1,point=t.point,witnesses=(t,),reach_nm=r) for t,r in zip(targets,(1000000,13000000))]
            self.goal_inventory_count=2;self.goal_index=0;self.search_extent=13000000
            self.reach=1000000;self.required_progress=self.reach;self.toward=(1,0)
            self.goal_heading=(1,0);self.leaf_net='signal';self.target_point=targets[0].point
        def edge_clear(self,state,new,turned):return new[1]==0 and new[0]>=0 and new[2]==0
        def commit(self,edges):
            self.calls.append(self.target_point)
            if self.target_point==(14000000,0):
                self.branch={'net':'signal'};return {'signal':{}},[],None
            return None,[],'Blocked nearest contact'
    return SearchGraph()


def test_farther_contact_gets_a_first_attempt_before_twelve_nearest_failures():
    search=synthetic_search();result=search.solve()
    assert result['accepted'],(search.calls,result)
    assert search.calls==[(2000000,0),(14000000,0)]
    assert result['stats']['native_goal_rejections']==1
    assert result['stats']['expanded']<=18000


def test_expired_shared_search_does_not_try_any_contact():
    search=synthetic_search();search.deadline=time.monotonic()-1
    result=search.solve();assert not result['accepted'] and search.calls==[]


def test_exhausted_shared_frontier_terminates_without_waiting_for_deadline():
    search=synthetic_search();search.edge_clear=lambda *args:False
    result=search.solve()
    assert not result['accepted'] and result['reason']=='frontier exhausted',result
    assert result['stats']['expanded']==1 and search.calls==[]


def test_native_proof_gets_the_shared_window_instead_of_a_short_contact_turn():
    search=synthetic_search();search.deadline=8;deadlines=[]
    namespace=search.solve.__func__.__globals__;original=namespace['time']
    namespace['time']=SimpleNamespace(monotonic=lambda:0)
    def commit(edges):deadlines.append(search.deadline);return {'signal':{}},[],None
    search.commit=commit
    try:result=search.solve()
    finally:namespace['time']=original
    assert result['accepted'] and deadlines==[8],deadlines


def test_unreached_early_contacts_do_not_discard_a_thirteenth_real_goal():
    search=synthetic_search();far=search.goals[1]
    first=search.goals[0]
    backwards=SimpleNamespace(leaf_net=first.leaf_net,heading=(-1,0),side=1,point=(-2000000,0),witnesses=first.witnesses,reach_nm=1000000)
    search.goals=[backwards]*12+[far];search.goal_inventory_count=13
    result=search.solve()
    assert result['accepted'] and search.calls==[(14000000,0)],(search.calls,result)
    assert result['stats']['goal_inventory']==13 and result['stats']['goals_offered']==13


def test_contact_reached_near_turn_boundary_keeps_a_native_proof_opportunity():
    search=synthetic_search();search.deadline=8;attempts=[]
    namespace=search.solve.__func__.__globals__;original=namespace['time']
    clock=SimpleNamespace(now=0);clock.monotonic=lambda:clock.now;namespace['time']=clock
    edge=search.edge_clear
    def advance(state,new,turned):
        clear=edge(state,new,turned)
        if clear:clock.now+=1.65
        return clear
    def commit(edges):attempts.append((search.target_point,search.deadline,clock.now));return {'signal':{}},[],None
    search.edge_clear=advance;search.commit=commit
    try:result=search.solve()
    finally:namespace['time']=original
    assert result['accepted'] and attempts==[((2000000,0),8,3.3)],attempts


def test_selected_real_face_witness_is_stable_when_fresh_uuids_reverse():
    ns=policy();ports=[{'net':'signal','exit_nm':[0,0],'heading_origin_nm':[-1000000,0],'collector_heading':[1,0]}]
    selected=[]
    for front_uid,plated_uid in (('a','z'),('z','a')):
        front=Item(front_uid,{0});plated=Item(plated_uid,{0,2})
        targets={'signal':[(item,{'item':item,'points':[item.position],'zone':False}) for item in (front,plated)]}
        goals=ns['branch_goals'](None,None,ports,['signal'],targets)
        selected.append(goals[0].witnesses[0].item.IsOnLayer(2))
    assert selected==[True,True],selected
