"""Check both subprocess and native-entrypoint boundaries of recovery."""
import ast
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest
from smilepcb.keyboard_lookahead import provisional_step,run_lookahead
from smilepcb.routing_quality import current_quality


def test_disposable_lookahead_sends_the_selected_quality_to_its_worker(tmp_path,monkeypatch):
    project=tmp_path/'project';project.mkdir();out=tmp_path/'out';out.mkdir()
    (project/'keyboard.kicad_pcb').write_text('untouched source')
    (project/'.smilepcb-keyboard.json').write_text(json.dumps({'plane_nets':[]}))
    calls=[]
    def worker(bridge,request,*args,**kwargs):
        calls.append((bridge,request))
        return dict(provisional_only=True,accepted_final=False,saved_trial=False,
                    before={'signal':1},after={'signal':1})
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_build',SimpleNamespace(NAME='keyboard',BRIDGE=Path('ordinary.py'),routing_transaction=None))
    monkeypatch.setitem(sys.modules,'smilepcb.lab_tools',SimpleNamespace(worker=worker,rule_checks=None))
    before={'pad_groups_missing':{'signal':1},'pad_group_gaps':1}
    result=run_lookahead(project,out,before,[],time.perf_counter()+400,lambda *_:None,routing_quality='turns')
    assert result is before and calls
    assert calls[0][0].name=='keyboard_lookahead_native.py'
    assert all(request['routing_quality']=='turns' for _,request in calls)
    assert (project/'keyboard.kicad_pcb').read_text()=='untouched source'


def native_entrypoint(tmp_path):
    # Execute the actual entrypoint/operation functions, stubbing CAD only.
    # This portable boundary check complements the real native shape fixtures.
    source=Path(__file__).resolve().parents[1]/'smilepcb/keyboard_lookahead_native.py'
    parsed=ast.parse(source.read_text(),filename=str(source))
    functions=ast.Module(body=[node for node in parsed.body if isinstance(node,ast.FunctionDef)],type_ignores=[])
    board=tmp_path/'keyboard.kicad_pcb';board.write_text('source')
    (tmp_path/'.smilepcb-keyboard.json').write_text(json.dumps({'plane_nets':[]}))
    observed=[]
    def repair(*args,**kwargs):observed.append(current_quality().name);return {}
    from smilepcb import keyboard_lookahead as policy
    env=dict(Path=Path,json=json,time=time,lookahead_limits=getattr(policy,'lookahead_limits',None),p=SimpleNamespace(LoadBoard=lambda _:object()),
             snapshot=lambda _:dict(tracks={}),validate=lambda *_:None,
             pad_gaps=lambda _:{'signal':1},repair_candidate=repair,provisional_step=provisional_step,
             require_provisional_frame=lambda *_:None,require_via_land_clearance=lambda *_args,**_kw:None)
    exec(compile(functions,str(source),'exec'),env)
    env['actual_owned_board']=env['owned_board']
    env['owned_board']=lambda _:(board,board)
    return env,observed


def test_native_lookahead_activates_and_resets_quality(tmp_path):
    env,observed=native_entrypoint(tmp_path)
    for mode in ('turns','legacy'):
        result=env['run'](dict(operation='provisional',routing_quality=mode,budget_s=1,depth=0))
        assert observed[-1]==mode
        assert result['routing_quality']==mode
        assert current_quality().name=='legacy'
    def broken(*_args,**_kwargs):
        assert current_quality().name=='turns'
        raise RuntimeError('repair failed')
    env['repair_candidate']=broken
    with pytest.raises(RuntimeError,match='repair failed'):
        env['run'](dict(operation='provisional',routing_quality='turns',budget_s=1,depth=0))
    assert current_quality().name=='legacy'


def test_generated_recovery_forwards_quality_to_disposable_lookahead(tmp_path,monkeypatch):
    from smilepcb.keyboard_recovery import recover_generated
    import smilepcb.keyboard_lookahead as lookahead
    (tmp_path/'.smilepcb-keyboard.json').write_text(json.dumps({'plane_nets':[]}))
    previous={'pad_group_gaps':1,'pad_groups_missing':{'signal':1}}
    def transaction(project,folder,repair,current,**kw):
        repair(project)
        return {'accepted':False,'independent':current}
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_build',SimpleNamespace(NAME='keyboard',routing_transaction=transaction))
    captured=[]
    def probe(*args,**kwargs):
        captured.append(kwargs['routing_quality'])
        return {'pad_group_gaps':0,'pad_groups_missing':{}}
    monkeypatch.setattr(lookahead,'run_lookahead',probe)
    def call(operation,**kwargs):return previous if operation=='verify' else {'accepted':False}
    result=recover_generated(tmp_path,tmp_path,previous,[],call,lambda *_:None,time.perf_counter()+500,routing_quality='turns')
    assert captured==['turns'] and result['pad_group_gaps']==0


def test_native_elastic_mode_selects_returns_from_actual_pending_roles(tmp_path):
    env,_=native_entrypoint(tmp_path)
    (tmp_path/'.smilepcb-keyboard.json').write_text(json.dumps({'plane_nets':['return']}))
    env['pad_gaps']=lambda _:{'return':1}
    selected=[]
    env['repair_candidate']=lambda _board,strategy,**_kw:selected.append(strategy) or {}
    env['run'](dict(operation='provisional',routing_quality='turns',budget_s=1,depth=0,elastic=True,gap_ceiling=3))
    assert selected==['plane_negotiate']


def test_conservative_native_request_cannot_override_its_gap_bound(tmp_path):
    env,observed=native_entrypoint(tmp_path)
    with pytest.raises(ValueError):
        env['run'](dict(operation='provisional',budget_s=1,depth=0,elastic=False,gap_ceiling=8))
    assert not observed


def allocated_trial(tmp_path,depth):
    env,_=native_entrypoint(tmp_path)
    source=tmp_path/'keyboard.kicad_pcb';root=tmp_path/'lookahead-owned';board=root/('hop-'+str(depth))/'project/keyboard.kicad_pcb'
    board.parent.mkdir(parents=True);board.write_text('disposable')
    marker={'token':'allocation-token','source_board':str(source),'elastic':True,'gap_ceiling':5}
    (root/'.smilepcb-lookahead-owner.json').write_text(json.dumps(marker))
    request={'board':str(board),'trial_root':str(root),'owner_token':'allocation-token','elastic':True,'gap_ceiling':5}
    return env['actual_owned_board'],request,board,source


def test_owned_elastic_allocation_can_use_its_eighth_candidate(tmp_path):
    owned,request,board,source=allocated_trial(tmp_path,7)
    assert owned(request)==(board,source)
    assert source.read_text()=='source'


def test_allocation_rejects_policy_and_envelope_mismatch(tmp_path):
    owned,request,_,source=allocated_trial(tmp_path,0)
    with pytest.raises(ValueError):owned({**request,'elastic':False})
    with pytest.raises(ValueError):owned({**request,'gap_ceiling':4})
    assert source.read_text()=='source'


def test_elastic_allocation_does_not_authorize_a_ninth_candidate(tmp_path):
    owned,request,_,_=allocated_trial(tmp_path,8)
    with pytest.raises(ValueError):owned(request)


@pytest.mark.parametrize('marker_value,request_value',[(5.0,5),(True,1)])
def test_allocation_marker_ceiling_uses_exact_integer_types(tmp_path,marker_value,request_value):
    owned,request,board,_=allocated_trial(tmp_path,0)
    marker=Path(request['trial_root'])/'.smilepcb-lookahead-owner.json'
    data=json.loads(marker.read_text());data['gap_ceiling']=marker_value;marker.write_text(json.dumps(data))
    with pytest.raises(ValueError):owned({**request,'gap_ceiling':request_value})


def test_old_allocation_marker_stays_conservative(tmp_path):
    owned,request,board,source=allocated_trial(tmp_path,0)
    marker=Path(request['trial_root'])/'.smilepcb-lookahead-owner.json'
    data=json.loads(marker.read_text());data.pop('elastic');data.pop('gap_ceiling');marker.write_text(json.dumps(data))
    legacy={k:v for k,v in request.items() if k not in ('elastic','gap_ceiling')}
    assert owned(legacy)==(board,source)
