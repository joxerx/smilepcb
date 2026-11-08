"""Default-preserving opt-in plumbing, with no Native generation."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import pytest
from smilepcb.keyboard_routing.source_policy import source_policy_arguments,legacy_fallback_provenance

ROOT=Path(__file__).parents[1]


def test_requested_policy_only_persists_opt_in_and_validates_boolean():
    assert source_policy_arguments({})=={}
    assert source_policy_arguments({'routing_preparation':{'coherent_via_banks':False}})=={}
    assert source_policy_arguments({'routing_preparation':{'coherent_via_banks':True}})=={'coherent_via_banks':True}
    with pytest.raises(ValueError):source_policy_arguments({'routing_preparation':{'coherent_via_banks':1}})


def test_api_and_cli_forward_opt_in_without_changing_defaults(monkeypatch):
    from smilepcb.stm32 import generate
    from smilepcb import cli
    seen=[];monkeypatch.setattr(generate,'_execute',lambda *args:seen.append(args[-1]) or {})
    generate.generate_keyboard('kle','cfg','tools','out',controller_source='donor')
    assert not seen[-1].coherent_via_banks
    generate.generate_keyboard('kle','cfg','tools','out',controller_source='donor',coherent_via_banks=True)
    assert seen[-1].coherent_via_banks
    with pytest.raises(ValueError):generate.generate_keyboard('k','c','t','o',controller_source='d',coherent_via_banks='true')
    monkeypatch.setattr(sys,'argv',['smilepcb','build','--kle','k','--config','c','--tools','t','--output','o','--controller-source','d','--coherent-via-banks'])
    cli.main();assert seen[-1].coherent_via_banks


@pytest.mark.parametrize('unified',[False,True])
@pytest.mark.parametrize('requested',[False,True])
def test_worker_request_only_adds_opt_in_key(requested,unified):
    source=ROOT/'smilepcb/stm32/generate.py';tree=ast.parse(source.read_text())
    call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and getattr(n.func,'id',None)=='worker'
        and any(isinstance(a,ast.Dict) and any(isinstance(k,ast.Constant) and k.value=='prepare_ordered_controller' for k in a.keys if k is not None) for a in n.args))
    request=next(a for a in call.args if isinstance(a,ast.Dict))
    args=NS(native_staged=True,placement_budget_s=220,joint_controller=False,joint_board=False,routing_candidates=1,
        optimize_matrix_gpio=True,process_profile='process',routing_quality='turns',coherent_via_banks=requested,unified_controller_plan=unified,component_corridors=unified)
    value=eval(compile(ast.Expression(request),str(source),'eval'),dict(args=args,project=Path('project'),core_file=Path('core'),
        mirror=False,rejected={False:[]},base_angle=None,index=0,prepared_core=None,STAGED_GENERATION_PROFILE='profile'))
    assert value.get('coherent_via_banks',False) is requested
    assert ('coherent_via_banks' in value) is requested
    assert ('unified_controller_plan' in value) is unified
    assert ('component_corridors' in value) is unified


def test_place_worker_options_validate_and_preserve_absent_default():
    source=ROOT/'smilepcb/stm32/place.py'
    definition=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_ordered_source_options')
    namespace={};exec(compile(ast.Module(body=[definition],type_ignores=[]),str(source),'exec'),namespace)
    fn=namespace['_ordered_source_options']
    assert fn({})=={} and fn({'coherent_via_banks':True})=={'coherent_via_banks':True}
    with pytest.raises(ValueError):fn({'coherent_via_banks':'true'})


def test_fallback_receipt_retains_failed_preference_without_coherence_claim():
    failed={'accepted':False,'reason':'finite source menu'};legacy={'accepted':True,'ports':[{'net':'signal'}]}
    result=legacy_fallback_provenance(legacy,failed)
    assert result['accepted'] and result['coherent_via_banks']['complete_coherence'] is False
    assert result['coherent_via_banks']['failed_preference_receipt']==failed
    assert 'coherent_via_banks' not in legacy and failed['reason']=='finite source menu'


def test_unified_api_cli_and_place_forward_only_explicit_true(monkeypatch):
    from smilepcb.stm32 import generate
    from smilepcb import cli
    seen=[];monkeypatch.setattr(generate,'_execute',lambda *args:seen.append(args[-1]) or {})
    generate.generate_keyboard('k','c','t','o',controller_source='d')
    assert seen[-1].unified_controller_plan is False
    generate.generate_keyboard('k','c','t','o',controller_source='d',unified_controller_plan=True)
    assert seen[-1].unified_controller_plan is True
    with pytest.raises(ValueError):generate.generate_keyboard('k','c','t','o',controller_source='d',unified_controller_plan=1)
    monkeypatch.setattr(sys,'argv',['smilepcb','build','--kle','k','--config','c','--tools','t','--output','o','--controller-source','d','--unified-controller-plan'])
    cli.main();assert seen[-1].unified_controller_plan is True
    source=ROOT/'smilepcb/stm32/place.py'
    node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_ordered_source_options')
    ns={};exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
    assert ns['_ordered_source_options']({'unified_controller_plan':True})=={'unified_controller_plan':True}
    with pytest.raises(ValueError):ns['_ordered_source_options']({'unified_controller_plan':'true'})


def test_component_corridor_api_cli_and_native_boundary_are_reachable(monkeypatch):
    from smilepcb.stm32 import generate
    from smilepcb import cli
    seen=[];monkeypatch.setattr(generate,'_execute',lambda *a:seen.append(a[-1]) or {})
    generate.generate_keyboard('k','c','t','o',controller_source='d',unified_controller_plan=True,component_corridors=True)
    assert seen[-1].component_corridors is True
    with pytest.raises(ValueError):generate.generate_keyboard('k','c','t','o',controller_source='d',component_corridors=True)
    with pytest.raises(ValueError):generate.generate_keyboard('k','c','t','o',controller_source='d',unified_controller_plan=True,component_corridors=1)
    monkeypatch.setattr(sys,'argv',['smilepcb','build','--kle','k','--config','c','--tools','t','--output','o','--controller-source','d','--unified-controller-plan','--component-corridors'])
    cli.main();assert seen[-1].component_corridors is True
    source=ROOT/'smilepcb/stm32/place.py';fn=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_ordered_source_options');ns={};exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),ns)
    assert ns['_ordered_source_options']({'component_corridors':True})=={'component_corridors':True}
    with pytest.raises(ValueError):ns['_ordered_source_options']({'component_corridors':'true'})
