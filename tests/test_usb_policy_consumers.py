"""Host caller-policy forwarding; no receipt may widen the caller's policy."""
import ast
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import pytest
from smilepcb.stm32 import core_pose_native as native

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('policy,effective',[(Ellipsis,20_000_000),(None,None),(35_000_000,35_000_000)])
@pytest.mark.parametrize('reuse',[False,True])
def test_ordered_preparation_forwards_caller_policy_without_reading_receipt(monkeypatch,policy,effective,reuse):
    seen=[];state={'tracks':{},'footprints':{}}
    def route(*a,**kw):seen.append(('route',kw['max_length_nm']));return dict(accepted=False,reason='stop before unrelated stages')
    def verify(*a,**kw):seen.append(('verify',kw['max_length_nm']));return dict(accepted=False,reason='stop before unrelated stages')
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.usb',NS(route_controller_usb=route))
    monkeypatch.setitem(sys.modules,'smilepcb.stm32.usb_placement',NS(verify_prepared_usb=verify))
    path=ROOT/'smilepcb/keyboard_routing/ordered_controller.py';node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='prepare_ordered_controller')
    scope=dict(__name__='smilepcb.keyboard_routing.ordered_controller',__package__='smilepcb.keyboard_routing',time=NS(monotonic=lambda:0),snapshot=lambda _:deepcopy(state))
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),scope)
    board=NS(GetFootprints=lambda:[],GetTracks=lambda:[],BuildConnectivity=lambda:None)
    receipt=dict(accepted=True,acceptance_context={'max_length_nm':None}) if reuse else None
    options={} if policy is Ellipsis else dict(usb_max_length_nm=policy)
    _,result=scope['prepare_ordered_controller'](board,{'parts':[{'ref':'J_UDB'}]},deadline=10,prepared_usb=receipt,**options)
    assert not result['accepted'] and seen==[('verify' if reuse else 'route',effective)]


@pytest.mark.parametrize('payload,expected',[({},Ellipsis),({'usb_max_length_nm':None},None),({'usb_max_length_nm':35_000_000},35_000_000)])
def test_placement_entry_forwards_request_default_sentinel_to_all_usb_consumers(payload,expected):
    tree=ast.parse((ROOT/'smilepcb/stm32/place.py').read_text())
    for name,keyword in [('select_usb_placement','max_length_nm'),('prepare_ordered_controller','usb_max_length_nm'),('verify_usb_pair','max_length_nm')]:
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id==name]
        assert calls,name
        for call in calls:
            value=next((q.value for q in call.keywords if q.arg==keyword),None)
            assert value is not None,(name,keyword)
            assert eval(compile(ast.Expression(body=value),'placement-policy','eval'),{'request':payload})==expected


@pytest.mark.parametrize('policy,effective',[(Ellipsis,20_000_000),(None,None),(35_000_000,35_000_000)])
def test_core_evaluation_forwards_same_request_policy_to_selection_and_reproof(monkeypatch,policy,effective):
    seen=[];state={'tracks':{},'footprints':{}}
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',NS(snapshot=lambda _:deepcopy(state)))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.controller_local',NS(prepare_controller_local=lambda *a,**kw:{}))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.power',NS(seed_controller_returns=lambda *a,**kw:dict(accepted=True)))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.controller_plan',NS(_local_subsets=lambda *a:{},verify_controller_local_subsets=lambda *a,**kw:None))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.interface_contract',NS(mcu_signal_nets=lambda _:set()))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_phases',NS(ordered_nets=lambda _:[('rows',[]),('columns',[])]))
    def select(*a,**kw):seen.append(('select',kw['max_length_nm']));return dict(accepted=True,usb={'accepted':True})
    def verify(*a,**kw):seen.append(('verify',kw['max_length_nm']));return a[1]
    monkeypatch.setitem(sys.modules,'smilepcb.stm32.usb_placement',NS(select_usb_placement=select,verify_prepared_usb=verify))
    monkeypatch.setattr(native,'_mechanics',lambda *a:{})
    monkeypatch.setattr(native,'_rules',lambda *a:{})
    runtime=native._Runtime.__new__(native._Runtime);runtime.context={};runtime.mechanics={};runtime.request={} if policy is Ellipsis else dict(usb_max_length_nm=policy)
    runtime.fixed_pose=dict(origin_nm=[1,2]);runtime._save=lambda _:None;runtime._probe=lambda *a:([],{})
    board=NS(GetFootprints=lambda:[NS(GetReference=lambda:'J_UDB')]);cfg=dict(parts=[dict(ref='J_UDB',region='controller')],plane_nets=[])
    scene=dict(pose={},expected_source_keys=[],resource=dict(board=board,cfg=cfg,assembly=dict(assembly_checks={'all':True})))
    runtime.evaluate(scene,deadline=10)
    assert seen==[('select',effective),('verify',effective)]


def test_selected_scene_resume_uses_request_policy_without_receipt_inference():
    tree=ast.parse((ROOT/'smilepcb/stm32/core_pose_native.py').read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='_Runtime');resume=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='resume')
    call=next(n for n in ast.walk(resume) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='verify_prepared_usb')
    assert any(q.arg=='max_length_nm' for q in call.keywords)
