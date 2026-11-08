"""Host scheduling proof; native pad/guard physics is a separate gate."""
import ast,itertools,math,sys
from pathlib import Path
from types import SimpleNamespace
import pytest


class Pad:
    def __init__(self,uid,point,ref,attribute=1):
        self.m_Uuid=SimpleNamespace(AsString=lambda:uid);self.point=point;self.ref=ref;self.attribute=attribute
    def GetNumber(self):return '1'
    def GetAttribute(self):return self.attribute
    def GetPosition(self):return SimpleNamespace(x=self.point[0],y=self.point[1])
    def GetParentFootprint(self):return SimpleNamespace(GetReference=lambda:self.ref)


class Copper:
    def __init__(self,uid):self.m_Uuid=SimpleNamespace(AsString=lambda:uid)


class Clock:
    now=0.
    def monotonic(self):return self.now


def scenario(monkeypatch,*,face=2,other_face=None,pad_count=2,smd=True,center_touches=True,
             pad_faces=None,leg_ok=True,leg_elapsed=.1,fallback_ok=True,source_guard=False,leg_error=None,emit_error=None,penalty_value=None):
    path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/native_graph.py'
    node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='route_components')
    clock=Clock();calls=[];emitted=[];parents=[0,1,0,1]
    left=(0,0);right=(10000000,0);other_face=face if other_face is None else other_face
    nodes=[dict(net='renamed arbitrary signal',item=Pad('a',left,'A',1 if smd else 2),points=[left],shapes={face:left},zone=False),
           dict(net='renamed arbitrary signal',item=Pad('b',right,'B'),points=[right],shapes={other_face:right},zone=False),
           dict(net='renamed arbitrary signal',item=Copper('via-a'),points=[(0,1000000)],shapes={0:None,2:None},zone=False),
           dict(net='renamed arbitrary signal',item=Copper('via-b'),points=[(10000000,1000000)],shapes={0:None,2:None},zone=False)]
    if not center_touches:nodes[0]['shapes'][face]=(-1000000,0)
    if pad_faces is not None:
        for n in nodes[:2]:n['shapes']={f:n['points'][0] for f in pad_faces}
    if pad_count>2:
        nodes.append(dict(net='renamed arbitrary signal',item=Pad('alias',(0,2000000),'A'),points=[(0,2000000)],shapes={face:(0,2000000)},zone=False));parents.append(0)
    def find(i):
        while parents[i]!=i:i=parents[i]
        return i
    def join(i,j):parents[find(i)]=find(j)
    groups={}
    for i in range(len(nodes)):groups.setdefault(find(i),[]).append(i)
    graph=dict(nodes=nodes,groups=groups,find=find,join=join)
    source={'net':'renamed arbitrary signal','pad_ids':['a'],'terminal_id':'via-a','added_ids':['via-a']}
    def contacts(graph,indices,toward):
        if source_guard and 0 in indices:return {((0,1000000),face)}
        return {(point,f) for i in indices for point in nodes[i]['points'] for f in nodes[i]['shapes']}
    policy=SimpleNamespace(ports=[source] if source_guard else [],live_ports=[source] if source_guard else [],contacts=contacts)
    router=SimpleNamespace(port_policy=policy,launch_policy=None,deadline=100.,grid_step=500000,connection_budget_s=6,search_budget_s=6,track_width=150000,added=[])
    router.expired=lambda:clock.now>=router.deadline
    router.refresh_interfaces=lambda graph:None
    def leg(a,b,f,net):
        calls.append({'kind':'leg','a':a,'b':b,'face':f,'deadline':router.deadline,'started':clock.now});clock.now+=leg_elapsed
        if leg_error:raise leg_error
        return [a,b] if leg_ok and not source_guard else None
    def add_path(points,f,net):
        calls.append({'kind':'emit','deadline':router.deadline,'started':clock.now});emitted.append((f,points));router.added.append('new-track')
        if emit_error:raise emit_error
    router.leg=leg;router.add_path=add_path
    if penalty_value is not None:
        router.routing_penalty=lambda shape,layer,net,via:penalty_value
        router.allow_penalty_fallback=False
    def search(candidate,starts,targets,net):
        calls.append({'kind':'fallback','starts':starts,'targets':targets,'deadline':candidate.deadline,'started':clock.now});clock.now+=.1
        candidate.last_search={'reason':'connected' if fallback_ok else 'frontier_exhausted'};return fallback_ok
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.grid_search',SimpleNamespace(search_many=search))
    native=SimpleNamespace(PAD=Pad,PAD_ATTRIB_SMD=1,F_Cu=0,B_Cu=2,
        SHAPE_CIRCLE=lambda point,radius:(point.x,point.y),SHAPE_SEGMENT=lambda a,b,width:(a,b,width),SHAPE=SimpleNamespace(Collide=lambda point,shape,gap:point==shape))
    ns={'__name__':'smilepcb.keyboard_routing._host_pad_face','__package__':'smilepcb.keyboard_routing','p':native,
        'itertools':itertools,'time':clock,'distance':math.dist,'xy':lambda q:(q.x,q.y),
        'vec':lambda q:SimpleNamespace(x=q[0],y=q[1]),'copper_graph':lambda *a,**k:graph}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
    return SimpleNamespace(run=lambda **kw:ns['route_components'](None,router,{'renamed arbitrary signal'},**kw),calls=calls,emitted=emitted,router=router,clock=clock)


@pytest.mark.parametrize('face',[0,2])
def test_real_shared_smd_face_precedes_free_preseeded_via_contacts(monkeypatch,face):
    case=scenario(monkeypatch,face=face);assert case.run()==1
    assert [c['kind'] for c in case.calls]==['leg','emit'],case.calls
    assert case.emitted==[(face,[(10000000,0),(0,0)])] or case.emitted==[(face,[(0,0),(10000000,0)])]
    attempt=case.router.graph_diagnostic['attempts'][0]
    assert attempt['searches']==[] and attempt['pad_face_proposal']['reason']=='connected'
    assert set(attempt['pad_face_proposal']['pad_ids'])=={'a','b'}
    assert case.router.deadline==100


def test_blocked_pad_face_keeps_unrestricted_multiface_fallback_and_same_end(monkeypatch):
    case=scenario(monkeypatch,leg_ok=False,leg_elapsed=2);assert case.run()==1
    assert [c['kind'] for c in case.calls]==['leg','fallback'],case.calls
    assert case.calls[0]['deadline']==case.calls[1]['deadline']==6
    assert {f for _,f in case.calls[1]['starts']}=={0,2}
    assert case.router.deadline==100 and not case.emitted


def test_pad_face_and_both_fallback_directions_share_absolute_connection_end(monkeypatch):
    case=scenario(monkeypatch,leg_ok=False,leg_elapsed=2,fallback_ok=False);assert case.run()==0
    assert [c['deadline'] for c in case.calls]==[6,6,6]
    assert [q['direction'] for q in case.router.graph_diagnostic['attempts'][0]['searches']]==['primary','reverse']
    assert case.router.deadline==100


def test_proposal_expiry_cannot_emit_or_launch_fallback(monkeypatch):
    case=scenario(monkeypatch,leg_elapsed=6);assert case.run()==0
    assert [c['kind'] for c in case.calls]==['leg'] and not case.emitted
    assert case.router.deadline==100


@pytest.mark.parametrize('kwargs',[{'pad_count':3},{'smd':False},{'other_face':0},{'pad_faces':(0,2)},{'center_touches':False}])
def test_ambiguous_or_nonphysical_pad_pair_falls_back(monkeypatch,kwargs):
    case=scenario(monkeypatch,**kwargs);assert case.run()==1
    assert [c['kind'] for c in case.calls]==['fallback'] and not case.emitted


@pytest.mark.parametrize('constraints',[{'required_pad_ids':{'a','b'}},{'interface_refs':{'A'}}])
def test_explicit_pad_or_interface_constraints_keep_existing_eligible_search(monkeypatch,constraints):
    case=scenario(monkeypatch);assert case.run(**constraints)==1
    assert [c['kind'] for c in case.calls]==['fallback']


def test_live_controller_source_raw_pad_leg_rejects_then_uses_verified_head_contacts(monkeypatch):
    case=scenario(monkeypatch,source_guard=True);assert case.run()==1
    assert [c['kind'] for c in case.calls]==['leg','fallback'] and not case.emitted
    assert case.calls[1]['starts']==[((0,1000000),2)],case.calls


def test_invariant_error_from_leg_restores_deadline_and_propagates(monkeypatch):
    case=scenario(monkeypatch,leg_error=ValueError('source changed'))
    with pytest.raises(ValueError,match='source changed'):case.run()
    assert case.router.deadline==100 and not case.emitted


def test_add_path_timeout_after_mutation_propagates_for_outer_transaction_rollback(monkeypatch):
    case=scenario(monkeypatch,emit_error=TimeoutError('controlled admission timeout'))
    with pytest.raises(TimeoutError,match='controlled admission timeout'):case.run()
    assert case.router.deadline==100 and case.emitted
    assert [c['kind'] for c in case.calls]==['leg','emit']


def test_nonzero_displacement_preference_keeps_weighted_search_instead_of_direct_pad_cut(monkeypatch):
    case=scenario(monkeypatch,penalty_value=5000000);assert case.run()==1
    assert [c['kind'] for c in case.calls]==['leg','fallback'] and not case.emitted
    assert case.calls[0]['deadline']==case.calls[1]['deadline']==6
    assert case.router.routing_penalty is not None and case.router.deadline==100


def test_zero_normal_keyboard_preference_keeps_same_pad_face_proposal(monkeypatch):
    case=scenario(monkeypatch,penalty_value=0);assert case.run()==1
    assert [c['kind'] for c in case.calls]==['leg','emit']
