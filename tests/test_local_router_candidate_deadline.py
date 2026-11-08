"""Candidate expiry is a failed proposal, never a source-policy waiver."""
from types import SimpleNamespace
import sys
import time
import pytest
from test_post_matrix_interface_scope import direct_router,emission_router


def budget_scene(monkeypatch,kind,*,emission=False):
    router,policy,emitted=emission_router() if emission else (*direct_router(),[])
    clock=SimpleNamespace(now=9);events=[];router.deadline=10
    if not hasattr(router,'board'):router.board=object()
    if not hasattr(router,'added'):router.added=[]
    policy.ports=[{'net':'control','pad_ids':['source'],'added_ids':['head']}];policy.resolved={('source',)}
    policy.accepts_segment=lambda *args,**kwargs:True;policy.accepts_via=lambda *args:True
    router.refresh_interfaces=lambda graph:events.append('publish')
    monkeypatch.setattr(time,'monotonic',lambda:clock.now)
    def graph(board,nets,deadline):
        events.append(('graph',deadline));assert deadline==10
        if kind in ('during','late'):clock.now=11
        if kind=='pre' or kind=='during':raise TimeoutError('Native graph budget exhausted: load conductors')
        if kind=='invariant':raise ValueError('Changed live controller interface source')
        return {}
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.native_graph',SimpleNamespace(copper_graph=graph))
    if kind=='pre':clock.now=10
    return router,clock,events,emitted


def candidate(router,method):
    return router.leg((0,0),(1000000,0),0,'control') if method=='leg' else router.via_clear((2000000,0),'control')


@pytest.mark.parametrize('method',['leg','via_clear'])
@pytest.mark.parametrize('kind',['pre','during','late'])
def test_expired_candidate_returns_failure_without_emission_or_deadline_renewal(monkeypatch,method,kind):
    router,clock,events,emitted=budget_scene(monkeypatch,kind)
    answer=candidate(router,method)
    assert answer is (None if method=='leg' else False)
    assert router.deadline==10 and emitted==[]
    assert len([e for e in events if isinstance(e,tuple)])==(0 if kind=='pre' else 1)
    assert ('publish' in events)==(kind=='late')


@pytest.mark.parametrize('method',['leg','via_clear'])
def test_healthy_candidate_keeps_its_actual_source_refresh(monkeypatch,method):
    router,clock,events,emitted=budget_scene(monkeypatch,'healthy')
    assert candidate(router,method)
    assert events==[('graph',10),'publish'] and router.deadline==10


@pytest.mark.parametrize('method',['leg','via_clear'])
def test_source_invariant_errors_are_not_candidate_budget_failures(monkeypatch,method):
    router,clock,events,emitted=budget_scene(monkeypatch,'invariant')
    with pytest.raises(ValueError,match='interface source'):candidate(router,method)
    assert events==[('graph',10)] and emitted==[]


@pytest.mark.parametrize('method',['add_path','add_via'])
@pytest.mark.parametrize('kind',['pre','during','late'])
def test_admission_never_emits_after_an_unproved_or_expired_refresh(monkeypatch,method,kind):
    router,clock,events,emitted=budget_scene(monkeypatch,kind,emission=True)
    with pytest.raises(TimeoutError):
        if method=='add_path':router.add_path([(0,0),(1000000,0)],0,'control')
        else:router.add_via((2000000,0),'control')
    assert emitted==[] and router.added==[] and router.deadline==10
    if kind=='pre':assert events==[]


@pytest.mark.parametrize('error',[TimeoutError('endpoint proof budget'),ValueError('source moved')])
def test_connect_distinguishes_endpoint_budget_from_invariant_failure(monkeypatch,error):
    router,clock,events,emitted=budget_scene(monkeypatch,'healthy')
    def endpoints(*args):raise error
    router.endpoints=endpoints
    pad=SimpleNamespace(GetNetname=lambda:'control',GetPosition=lambda:SimpleNamespace(x=0,y=0))
    if isinstance(error,TimeoutError):assert router.connect(pad,pad) is False
    else:
        with pytest.raises(ValueError,match='source moved'):router.connect(pad,pad)
    assert emitted==[]


def spoke_kernel(error):
    import ast,math
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_routing/via_spoke.py'
    node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='rehome_spoke')
    uid=lambda value:SimpleNamespace(AsString=lambda:value)
    via=SimpleNamespace(m_Uuid=uid('via'),GetPosition=lambda:SimpleNamespace(x=4_000_000,y=0),GetNetname=lambda:'control',GetWidth=lambda layer:500000,GetDrillValue=lambda:200000,IsOnLayer=lambda layer:True,GetEffectiveShape=lambda layer:object())
    contact=SimpleNamespace(m_Uuid=uid('contact'),IsOnLayer=lambda layer:True,GetNetname=lambda:'control',GetEffectiveShape=lambda layer:object())
    branch=SimpleNamespace(m_Uuid=uid('branch'),GetLength=lambda:4000000,GetWidth=lambda:150000)
    pad=SimpleNamespace(GetPosition=lambda:SimpleNamespace(x=0,y=0),GetEffectiveShape=lambda layer:SimpleNamespace(BBox=lambda:SimpleNamespace(GetWidth=lambda:300000,GetHeight=lambda:300000)))
    deleted=[];board=SimpleNamespace(GetCopperLayerCount=lambda:2,GetTracks=lambda:[via,contact],GetFootprints=lambda:[],Delete=deleted.append)
    def endpoints(*args):raise error
    router=SimpleNamespace(via_width=500000,drill=200000,endpoints=endpoints)
    native=SimpleNamespace(F_Cu=0,B_Cu=2,FromMM=lambda mm:round(mm*1e6),PCB_ARC=type('Arc',(),{}),SHAPE=SimpleNamespace(Collide=lambda *args:True))
    env={'p':native,'time':time,'math':math,'spoke':lambda *args:(pad,[branch]),'LocalRouter':lambda *args,**kwargs:router,'xy':lambda v:(v.x,v.y),'vec':lambda v:v,'octile_paths':lambda *args:[],'distance':math.dist}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),env)
    return lambda:env['rehome_spoke'](board,via,0,protected=set(),deadline=time.monotonic()+1),deleted


def test_spoke_endpoint_budget_exhaustion_returns_no_move_before_mutation():
    call,deleted=spoke_kernel(TimeoutError('endpoint graph budget'))
    assert call() is None and deleted==[]


def test_spoke_endpoint_source_invariant_remains_a_hard_failure():
    call,deleted=spoke_kernel(ValueError('source moved'))
    with pytest.raises(ValueError,match='source moved'):call()
    assert deleted==[]
