"""HOST execution of the actual public wrapper/backend budget boundary."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from smilepcb.usb_policy import configured_usb_max_length_nm
ROOT=Path(__file__).resolve().parents[1]


def boundary(monkeypatch,tmp_path):
    from smilepcb.keyboard_routing import field_contract,interface_contract
    source=ROOT/'smilepcb/keyboard_native.py'
    nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name in {'run','_run'}]
    state={'tracks':{'head':{'net':'control'}},'footprints':{'chip':{'pads':[{'uuid':'pad','net':'control'}]}}}
    port={'net':'control','pad_ids':['pad'],'added_ids':['head'],
          'source_shapes':{'head':{'net':'control'},'pad':{'uuid':'pad','net':'control'}}}
    (tmp_path/'.smilepcb-keyboard.json').write_text(json.dumps({'routing_preparation':{'mode':'ordered-banks-v2'}}))
    (tmp_path/interface_contract.MARKER).write_text('{}')
    clock=[100.0];seen={};monkeypatch.setattr(time,'monotonic',lambda:clock[0])
    def interface(*args,**kwargs):
        clock[0]+=3
        return {'ports':[port],'source_tracks':deepcopy(state['tracks']),'resolved':[]}
    monkeypatch.setattr(interface_contract,'load_verified_interfaces',interface)
    monkeypatch.setattr(field_contract,'load',lambda path:{'scope':'matrix-with-controller'})
    monkeypatch.setattr(field_contract,'guarded',lambda request,fn:fn(request))
    def repair(request,*,deadline):
        seen['inner_deadline']=deadline;clock[0]=deadline+7
        return {'accepted':False}
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.joint_repair_native',SimpleNamespace(repair_saved=repair))
    def admit(*args,**kwargs):seen['outer_deadline']=kwargs['deadline']
    env={'Path':Path,'json':json,'p':SimpleNamespace(LoadBoard=lambda path:object()),
         'w':SimpleNamespace(snapshot=lambda board:deepcopy(state)),'validate':lambda *args:None,
         '_admit_interface_delta':admit}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),env)
    actual_backend=env['_run']
    def dispatch(request,**kwargs):
        seen['preflight']=kwargs.get('preflight_elapsed_s',0)
        if request['operation']=='joint_repair':return actual_backend(request,**kwargs)
        return {'accepted':False}
    env['_run']=dispatch
    def run(operation='joint_repair',**kwargs):
        return env['run']({'operation':operation,'board':str(tmp_path/'keyboard.kicad_pcb'),**kwargs})
    return run,seen


@pytest.mark.parametrize('budget,expected',[(None,82),(3.5,3.5),(120,120),(150,150)])
def test_joint_request_budget_deducts_preflight_and_retains_eight_second_postguard(monkeypatch,tmp_path,budget,expected):
    run,seen=boundary(monkeypatch,tmp_path)
    run(**({} if budget is None else {'budget_s':budget}))
    assert seen=={'preflight':3,'inner_deadline':100+expected,'outer_deadline':108+expected}


@pytest.mark.parametrize('budget',[0,-1,150.0001,float('inf'),float('-inf'),float('nan'),True,False,'150',None])
def test_invalid_joint_budget_never_enters_worker(monkeypatch,tmp_path,budget):
    run,seen=boundary(monkeypatch,tmp_path)
    with pytest.raises(ValueError):run(budget_s=budget)
    assert 'inner_deadline' not in seen


def test_nonjoint_request_keeps_ninety_second_outer_guard(monkeypatch,tmp_path):
    run,seen=boundary(monkeypatch,tmp_path);run(operation='components',budget_s=150)
    assert seen=={'preflight':3,'outer_deadline':190}


def test_preflight_still_consumes_joint_worker_budget(monkeypatch,tmp_path):
    run,seen=boundary(monkeypatch,tmp_path)
    with pytest.raises(TimeoutError):run(budget_s=0.5)
    assert 'inner_deadline' not in seen


def host_callback(kind,monkeypatch,tmp_path,remaining):
    from smilepcb.keyboard_routing import generated_provenance
    filename={'build':'keyboard_build.py','complete':'stm32/complete.py','route':'stm32/route.py','global':'stm32/route_global.py'}[kind]
    source=ROOT/'smilepcb'/filename
    tree=ast.parse(source.read_text())
    call=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='call')
    nodes=[call]
    if kind=='build':nodes.insert(0,next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='remaining'))
    seen=[]
    def worker(backend,bridge,request,log,**kwargs):
        seen.append((request,kwargs['timeout_s']))
        return {'generation_profile':'test','accepted':False}
    monkeypatch.setattr(generated_provenance,'tracked_worker',worker)
    monkeypatch.setattr(time,'perf_counter',lambda:100)
    env={'configured_usb_max_length_nm':configured_usb_max_length_nm,'cfg':{},'__package__':'smilepcb','Path':Path,'time':time,'json':json,'encode':json.dumps,
         'worker':object(),'BRIDGE':'bridge','board':tmp_path/'keyboard.kicad_pcb',
         'output':tmp_path,'out':tmp_path,'deadline':100+remaining,'route_attempts':{},
         'counts':{},'history':[],'quality':'turns','profile':'test','PROFILE':'test',
         'tracked_worker':worker,'seed_receipt':{'generation_profile':'test'}}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),env)
    return (lambda operation,tag,**kwargs:env['call']('bridge',operation,tag,**kwargs)) if kind=='global' else env['call'],seen


@pytest.mark.parametrize('kind',['build','complete','route','global'])
@pytest.mark.parametrize('budget,remaining,expected',[(150,200,158),(82,200,90),(150,100,100)])
def test_actual_worker_callbacks_preserve_joint_guard_and_global_deadline(kind,budget,remaining,expected,monkeypatch,tmp_path):
    call,seen=host_callback(kind,monkeypatch,tmp_path,remaining)
    options={'worker_timeout_s':remaining} if kind in ('route','global') else {}
    call('joint_repair',tag='bounded',budget_s=budget,**options)
    assert seen[0][0]['budget_s']==budget and seen[0][1]==expected


@pytest.mark.parametrize('kind',['build','complete','route','global'])
def test_actual_nonjoint_worker_callbacks_keep_default_ninety_seconds(kind,monkeypatch,tmp_path):
    call,seen=host_callback(kind,monkeypatch,tmp_path,200)
    call('complete',tag='ordinary',budget_s=150)
    assert seen[0][1]==90


@pytest.mark.parametrize('kind',['recovery','preplane'])
@pytest.mark.parametrize('remaining,expected,copy_s',[(95,82,0),(120,107,0),(200,150,0),(140,107,10),(100,None,3)])
def test_real_recovery_scheduler_allocates_joint_budget_after_copy(kind,remaining,expected,copy_s,monkeypatch,tmp_path):
    from smilepcb import keyboard_recovery
    from smilepcb.stm32 import route
    from smilepcb.keyboard_routing import field_contract
    clock=[100.0];monkeypatch.setattr(time,'perf_counter',lambda:clock[0])
    closed=[False];seen=[]
    missing={'pad_group_gaps':1,'pad_groups_missing':{'SIG':1}}
    complete={'pad_group_gaps':0,'pad_groups_missing':{}}
    def transaction(project,folder,repair,before,**kwargs):
        clock[0]+=copy_s
        result=repair(Path(project))
        return {'accepted':result['pad_group_gaps']==0}
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_build',SimpleNamespace(NAME='keyboard',routing_transaction=transaction))
    monkeypatch.setattr(field_contract,'load',lambda project:{'schema':1})
    monkeypatch.setattr(route,'generated_ids',lambda path:['positive'])
    board=tmp_path/'keyboard.kicad_pcb';board.write_text('(kicad_pcb)')
    (tmp_path/'.smilepcb-keyboard.json').write_text(json.dumps({'plane_nets':[]}))
    (tmp_path/'.smilepcb-field.json').write_text('{}')
    output=tmp_path/'output';output.mkdir()
    def call(operation,**kwargs):
        seen.append((operation,kwargs))
        if operation=='joint_repair':closed[0]=True
        return {'accepted':operation=='joint_repair',**(complete if closed[0] else missing)}
    if kind=='recovery':
        def run():return keyboard_recovery.recover_generated(tmp_path,output,missing,[],call,lambda q:None,100+remaining,generated_ids=['positive'])
    else:
        def run():return route.recover_preplane_signals(tmp_path,output,missing,call,plane_nets=[],deadline=100+remaining)
    if expected is None:
        if kind=='recovery':
            with pytest.raises(TimeoutError):run()
        else:assert run()==missing
        assert all(operation!='joint_repair' for operation,options in seen)
        return
    result=run()
    joint=next(options for operation,options in seen if operation=='joint_repair')
    assert result['pad_group_gaps']==0 and joint['budget_s']==expected
    assert expected+8<=100+remaining-clock[0]-5
    if kind=='preplane':assert joint['worker_timeout_s']==expected+8
    assert next(options for operation,options in seen if operation=='complete').get('budget_s',82)==82
