"""The public build must carry an explicit USB geometry policy end to end."""
import ast
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest
from smilepcb import usb_policy

ROOT=Path(__file__).parents[1]

@pytest.mark.parametrize('text,expected',[('20',20_000_000),('35.5',35_500_000),('none',None)])
def test_cli_policy_uses_mm_without_changing_default(text,expected,monkeypatch):
    from smilepcb import cli
    from smilepcb.stm32 import generate
    seen=[]
    monkeypatch.setattr(generate,'_execute',lambda *a:seen.append(a[-1]) or {})
    monkeypatch.setattr(sys,'argv',['smilepcb','build','--kle','k','--config','c','--tools','t','--output','o','--controller-source','d','--usb-max-length-mm',text])
    cli.main()
    assert seen[-1].usb_max_length_nm==expected


def test_api_resolves_legacy_default_and_rejects_invalid_before_execution(monkeypatch):
    from smilepcb.stm32 import generate
    seen=[]
    monkeypatch.setattr(generate,'_execute',lambda *a:seen.append(a[-1]) or {})
    generate.generate_keyboard('k','c','t','o',controller_source='d')
    assert seen[-1].usb_max_length_nm==20_000_000
    generate.generate_keyboard('k','c','t','o',controller_source='d',usb_max_length_nm=None)
    assert seen[-1].usb_max_length_nm is None
    for bad in (True,0,-1,1.2,'none'):
        with pytest.raises(ValueError):generate.generate_keyboard('k','c','t','o',controller_source='d',usb_max_length_nm=bad)
    assert len(seen)==2

@pytest.mark.parametrize('text',['0','-1','nan','inf','0.0000001','x'])
def test_invalid_mm_policy_is_rejected(text):
    with pytest.raises(ValueError):usb_policy.parse_usb_max_length_mm(text)


def test_configured_policy_requires_explicit_value_not_old_proof():
    assert usb_policy.configured_usb_max_length_nm({})==20_000_000
    assert usb_policy.configured_usb_max_length_nm({'routing_preparation':{'usb_max_length_nm':None}}) is None
    assert usb_policy.configured_usb_max_length_nm({'usb_proof':{'max_length_nm':None}})==20_000_000
    with pytest.raises(ValueError):usb_policy.configured_usb_max_length_nm({'routing_preparation':{'usb_max_length_nm':False}})


def test_placement_request_carries_declared_policy():
    source=ROOT/'smilepcb/stm32/generate.py'
    tree=ast.parse(source.read_text())
    call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and getattr(n.func,'id',None)=='worker' and any(isinstance(a,ast.Dict) and any(isinstance(k,ast.Constant) and k.value=='prepare_ordered_controller' for k in a.keys if k is not None) for a in n.args))
    request=next(a for a in call.args if isinstance(a,ast.Dict))
    args=SimpleNamespace(native_staged=True,placement_budget_s=220,joint_controller=False,joint_board=False,routing_candidates=1,optimize_matrix_gpio=True,process_profile='process',routing_quality='turns',coherent_via_banks=False,unified_controller_plan=True,usb_max_length_nm=None)
    value=eval(compile(ast.Expression(request),str(source),'eval'),dict(args=args,project=Path('p'),core_file=Path('c'),mirror=False,rejected={False:[]},base_angle=None,index=0,prepared_core=None,STAGED_GENERATION_PROFILE='profile'))
    assert 'usb_max_length_nm' in value and value['usb_max_length_nm'] is None

@pytest.mark.parametrize('stage',['route','complete'])
def test_native_downstream_receives_model_policy(stage,tmp_path,monkeypatch):
    import time
    from smilepcb.keyboard_routing import generated_provenance
    seen=[]
    monkeypatch.setattr(generated_provenance,'tracked_worker',lambda worker,script,request,*a,**k:seen.append(request) or {'generation_profile':'profile'})
    source=ROOT/('smilepcb/stm32/'+stage+'.py')
    fn=next(n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.FunctionDef) and n.name=='call')
    namespace=dict(time=time,tracked_worker=generated_provenance.tracked_worker,worker=None,BRIDGE=Path('bridge'),board=tmp_path/'keyboard.kicad_pcb',out=tmp_path,PROFILE='profile',profile='profile',quality='turns',history=[],counts={},deadline=time.perf_counter()+90,json=__import__('json'),cfg={'routing_preparation':{'usb_max_length_nm':None}})
    # Permit the implementation to use the shared policy resolver imported by its module.
    namespace['configured_usb_max_length_nm']=getattr(usb_policy,'configured_usb_max_length_nm',lambda cfg:20_000_000)
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),namespace)
    namespace['call']('verify','policy')
    assert 'usb_max_length_nm' in seen[-1] and seen[-1]['usb_max_length_nm'] is None


def test_legacy_usb_worker_passes_policy_to_pair_router():
    source=ROOT/'smilepcb/stm32/route_native.py';tree=ast.parse(source.read_text())
    call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and getattr(n.func,'id',None)=='route_controller_usb')
    seen=[]
    eval(compile(ast.Expression(call),str(source),'eval'),{'b':None,'time':SimpleNamespace(monotonic=lambda:0),'request':{'usb_max_length_nm':None},'route_controller_usb':lambda *a,**k:seen.append(k)})
    assert 'max_length_nm' in seen[-1] and seen[-1]['max_length_nm'] is None


def test_global_downstream_receives_declared_policy(tmp_path,monkeypatch):
    import time,json
    source=ROOT/'smilepcb/stm32/route_global.py'
    fn=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='call')
    seen=[]
    namespace=dict(time=time,tracked_worker=lambda worker,script,request,*a,**k:seen.append(request) or {},worker=None,board=tmp_path/'keyboard.kicad_pcb',out=tmp_path,seed_receipt={'generation_profile':'profile'},quality='turns',json=json,cfg={'routing_preparation':{'usb_max_length_nm':None}},configured_usb_max_length_nm=usb_policy.configured_usb_max_length_nm)
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),namespace)
    namespace['call'](Path('bridge'),'verify','policy')
    assert 'usb_max_length_nm' in seen[-1] and seen[-1]['usb_max_length_nm'] is None

@pytest.mark.parametrize('policy,length_mm,accepted',[(Ellipsis,19,True),(Ellipsis,72,False),(None,72,True),(75_000_000,72,True),(35_000_000,72,False)])
def test_native_inspector_uses_same_declared_cap(policy,length_mm,accepted):
    source=ROOT/'smilepcb/stm32/route_native.py';tree=ast.parse(source.read_text())
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.Dict) and any(isinstance(k,ast.Constant) and k.value=='usb_recipe_pass' for k in n.keys if k is not None))
    request={} if policy is Ellipsis else {'usb_max_length_nm':policy}
    board=SimpleNamespace(GetLayerName=lambda layer:'B.Cu',GetCopperLayerCount=lambda:2)
    result=eval(compile(ast.Expression(node),str(source),'eval'),dict(gaps={},pins=True,b=board,layer=31,lengths={'USB_DP':length_mm,'USB_DM':length_mm},usb_layers={'B.Cu'},usb_vias=0,request=request,resolve_usb_max_length_nm=usb_policy.resolve_usb_max_length_nm,usb_length_allowed=usb_policy.usb_length_allowed))
    assert result['usb_recipe_pass'] is accepted
