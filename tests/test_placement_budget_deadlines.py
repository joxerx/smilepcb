"""Host clocks exercise real orchestration; no native routing is imported."""
import ast
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]


def function(path,name,namespace):
    tree=ast.parse((ROOT/path).read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(nodes)==1, 'Missing deadline helper'
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ROOT/path),'exec'),namespace)
    return namespace[name]


@pytest.mark.parametrize('budget,staged,expected',[(220,True,(320,320)),(1200,True,(320,1300)),(80,False,(180,180))])
def test_placement_deadlines_share_original_start_and_preserve_preparation(budget,staged,expected):
    fn=function('smilepcb/stm32/place.py','_placement_deadlines',{})
    assert fn(100,budget,staged)==expected


@pytest.mark.parametrize('late,expected', [(None, 149), (150, 149), (360, 175)])
def test_field_can_use_explicit_late_matrix_budget(late, expected):
    cutoff = function('smilepcb/keyboard_routing/matrix_stage.py', '_field_cutoff', {})
    # Early phases consumed most of the initial window before the field begins.
    assert cutoff(110, 170, late) == expected


def module(monkeypatch,name,**values):
    obj=ModuleType('smilepcb.keyboard_routing.'+name);obj.__dict__.update(values)
    monkeypatch.setitem(sys.modules,obj.__name__,obj);return obj


@pytest.mark.parametrize('preplanned',[False,True])
@pytest.mark.parametrize('coherent',[False,True])
@pytest.mark.parametrize('late,expected,matrix_ok',[(None,209,True),(1191,1189,True),(1191,1189,False)])
def test_collector_keeps_source_clock_but_forwards_original_late_matrix_cutoff(monkeypatch,late,expected,matrix_ok,coherent,preplanned):
    from contextlib import nullcontext
    clock=SimpleNamespace(now=100);clock.monotonic=lambda:clock.now
    chip=SimpleNamespace(GetReference=lambda:'U_MCU',GetLayer=lambda:2,Pads=lambda:[])
    board=SimpleNamespace(GetFootprints=lambda:[chip],GetTracks=lambda:[],GetDesignSettings=lambda:SimpleNamespace(m_TrackMinWidth=150000,m_MinClearance=150000),BuildConnectivity=lambda:None)
    ports=[{'net':'R','added_ids':[],'pad_ids':['row-pad']},{'net':'C','added_ids':[],'pad_ids':['column-pad']}];calls={'source_calls':0}
    def source(*args,**kw):calls['source_calls']+=1;calls['source']=kw['deadline'];clock.now=125;return {'accepted':True,'ports':deepcopy(ports)}
    def take(*args,**kw):calls['take']=True;clock.now=125;return {'accepted':True,'ports':deepcopy(ports)}
    def matrix(*args,**kw):calls['matrix']=kw;calls['candidate_cfg']=deepcopy(args[1]);clock.now=1100 if late else 200;return {'complete':matrix_ok,'controller_connected':matrix_ok}
    def capture(board,cfg,ports,**kw):calls['capture']=kw;return {'schema':1,'ports':deepcopy(ports)}
    def verify(*args,**kw):calls['interface_verify']=kw;return {'resolved':[('row-pad',),('column-pad',)]}
    module(monkeypatch,'interface_contract',capture_interfaces=capture,verify_interfaces=verify)
    module(monkeypatch,'generation',current_generation_profile=lambda:SimpleNamespace(name='dsn-process-v2'))
    module(monkeypatch,'conditional_banks',certify_generated_heads=lambda *a,**k:object(),copy_native_tracks=lambda b:{},_source_physics=lambda *a:'empty-clock-fixture')
    module(monkeypatch,'modules',connect_key_modules=lambda *a,**k:{'accepted':True})
    module(monkeypatch,'power',connect_controller_return_tree=lambda *a,**k:{'accepted':True})
    ns={'__package__':'smilepcb.keyboard_routing','time':clock,'snapshot':lambda b:{'tracks':{},'footprints':{}},
        'verified_return_tree_ownership':lambda *a,**kw:None,'merge_provisional_ownership':lambda *a:None,
        'transient_access_ids':lambda *a:set(), 'reserve_launches':lambda *a:nullcontext(),'route_controller_ports':source,
        'ordered_nets':lambda c:[('rows',['R']),('columns',['C'])],
        'collector_assignment':lambda *a:({},[{'collector_heading':[0,1],'signal_peel_order':['R','C']}]),'apply_exit_assignment':lambda b,c,ps,**kw:(deepcopy(c),{'ports':deepcopy(ps)}),
        'matrix_stage':SimpleNamespace(prepare_matrix=matrix),'deepcopy':deepcopy,'Path':Path,'traceback':__import__('traceback'),'p':SimpleNamespace(F_Cu=0,B_Cu=2)}
    fn=function('smilepcb/keyboard_routing/collector_preparation.py','prepare_collector_matrix',ns)
    _,result=fn(board,{'plane_nets':['GND'],'parts':[{'ref':'U_MCU','region':'controller'}]},controller_refs={'U_MCU'},nets={'R','C'},targets={},fixed_ports=[],deadline=211,recovery_deadline=late,coherent_via_banks=coherent,**({'preplanned_source':SimpleNamespace(take=take,matrix_delta=lambda *a,**kw:set())} if preplanned else {}))
    assert result['candidate_accepted'] is matrix_ok
    if preplanned:assert calls['take']
    else:assert calls['source']==135
    assert calls['candidate_cfg']['routing_preparation'].get('coherent_via_banks',False) is coherent
    assert ('coherent_via_banks' in calls['candidate_cfg']['routing_preparation']) is coherent
    assert calls['source_calls']==(0 if preplanned else 1)  # Late failure cannot restart the elapsed source clock.
    assert calls['matrix']['deadline']==209 and calls['matrix']['recovery_deadline']==expected
    assert calls['capture']['deadline']==209
    if matrix_ok:
        assert calls['interface_verify']=={'deadline':late or 211,'retire_completed':True}
    else:assert 'interface_verify' not in calls


def test_ordered_success_preserves_original_receipt_and_late_field_proof_deadline(monkeypatch):
    from test_controller_return_order import caller,Board
    fn,_,seen=caller(monkeypatch,'dsn-process-v2',matrix_failure=True)
    clock=SimpleNamespace(now=100);clock.monotonic=lambda:clock.now;fn.__globals__['time']=clock;calls={};original={'schema':1}
    cp=sys.modules['smilepcb.keyboard_routing.collector_preparation']
    def prepared(board,cfg,**kw):
        calls['collector']=kw;clock.now=1100;cfg['routing_preparation']={'mode':'ordered-banks-v2'}
        return cfg,{'candidate_accepted':True,'assignment':{'ports':[]},'matrix':{},'prerequisite_ids':[],'interface':original}
    cp.prepare_collector_matrix=prepared
    fn.__globals__['capture_interfaces']=lambda *a,**kw:calls.setdefault('receipt',kw)
    module(monkeypatch,'field_contract',capture=lambda *a,**kw:{'scope':'matrix-with-controller','terminals':{'r':['rows','ROW'],'c':['columns','COL']}},verify_native=lambda *a,**kw:calls.setdefault('verify',kw))
    cfg={'parts':[{'ref':'U_MCU','region':'controller'}],'plane_nets':['GND']}
    _,result=fn(Board(),cfg,deadline=219,recovery_deadline=1199,complete_matrix=True)
    assert result['accepted']
    assert seen[0]['deadline']==115
    assert calls['collector']['deadline']==211 and calls['collector']['recovery_deadline']==1191
    assert calls['verify']['deadline']==1199 and 'receipt' not in calls
    assert result['interface'] is original
