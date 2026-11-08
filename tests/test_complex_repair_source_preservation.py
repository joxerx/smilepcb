"""Complex repair wrapper source boundaries; HOST-only native boundary doubles."""
import ast
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]


def scene(monkeypatch,tmp_path,*,operation='complete',full=True,retired='missing',change=None,accepted=True):
    from smilepcb.keyboard_routing import field_contract,interface_contract
    from smilepcb.keyboard_routing.interface_launches import current_launches
    def track(net,start,end):return {'class':'PCB_TRACK','net':net,'start':start,'end':end,'width':150000,'layer':0,'locked':False}
    original={
        'head-a':track('live',[0,0],[1,0]),'head-b':track('closed-intact',[0,1],[1,1]),
        'head-c':track('closed-retired',[0,2],[1,2]),'tail-b':track('closed-intact',[1,1],[8,1]),
        'tail-c':track('closed-retired',[1,2],[8,2]),'unrelated':track('other',[3,3],[9,3])}
    pads=[{'uuid':'pad-'+key,'number':str(i),'net':net,'position':[0,i]} for i,(key,net) in enumerate([('a','live'),('b','closed-intact'),('c','closed-retired')])]
    state={'tracks':deepcopy(original),'footprints':{'chip':{'pads':pads}}}
    ports=[]
    for key,net in [('a','live'),('b','closed-intact'),('c','closed-retired')]:
        pad=next(d for d in pads if d['uuid']=='pad-'+key)
        ports.append({'net':net,'pad_ids':[pad['uuid']],'added_ids':['head-'+key],'terminal_id':'head-'+key,
            'source_shapes':{'head-'+key:deepcopy(original['head-'+key]),pad['uuid']:deepcopy(pad)}})
    if retired=='missing':state['tracks'].pop('head-c')
    elif retired=='trimmed':state['tracks']['head-c']['end']=[.8,2]
    before=deepcopy(state);observed=[]
    (tmp_path/'.smilepcb-keyboard.json').write_text(json.dumps({'routing_preparation':{'mode':'ordered-banks-v2'}}))
    (tmp_path/interface_contract.MARKER).write_text('{}')
    def verify(*args,**kwargs):
        return {'ports':deepcopy(ports),'resolved':[['pad-b'],['pad-c']],
            'source_tracks':{'head-a':deepcopy(original['head-a'])},'native_contacts_exact':True}
    monkeypatch.setattr(interface_contract,'load_verified_interfaces',verify)
    monkeypatch.setattr(field_contract,'load',lambda project:{'scope':'matrix-with-controller' if full else 'key-field'})
    def guarded(request,fn):
        try:return fn(request)
        except BaseException:
            state.clear();state.update(deepcopy(before));raise
    monkeypatch.setattr(field_contract,'guarded',guarded)
    def apply(checked,**kwargs):
        observed.append({'protected':set(checked.get('protected_ids',())),'ports':deepcopy(list(current_launches())),'preflight_elapsed_s':kwargs.get('preflight_elapsed_s',0)})
        if change:change(state)
        return {'accepted':accepted}
    path=ROOT/'smilepcb/keyboard_native.py';node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='run')
    env={'Path':Path,'json':json,'p':SimpleNamespace(LoadBoard=lambda path:object()),'_run':apply,
        'w':SimpleNamespace(snapshot=lambda board:deepcopy(state)),
        # The separate existing backend first-contact tests exercise admission.
        # This fixture isolates wrapper preservation even if that gate is skipped.
        '_admit_interface_delta':lambda *args,**kwargs:None}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),env)
    def run():return env['run']({'operation':operation,'board':str(tmp_path/'keyboard.kicad_pcb'),'protected_ids':['unrelated']})
    return run,state,before,ports,observed


@pytest.mark.parametrize('operation',['complete','completion_candidates'])
def test_complex_repair_keeps_closed_intact_head_and_retired_net_hard(monkeypatch,tmp_path,operation):
    run,state,before,ports,observed=scene(monkeypatch,tmp_path,operation=operation)
    run()
    assert observed[0]['protected']=={'unrelated','head-a','head-b','tail-c'}
    assert state==before
    assert observed[0]['ports']==ports, 'Protection must not rewrite source metadata from fragments'


@pytest.mark.parametrize('operation',['cleanup','components'])
def test_ordinary_completed_net_retirement_and_branching_are_not_frozen(monkeypatch,tmp_path,operation):
    run,state,before,ports,observed=scene(monkeypatch,tmp_path,operation=operation)
    run()
    assert observed[0]['protected']=={'unrelated','head-a'}


def test_partial_legacy_handoff_does_not_invent_complex_source_ownership(monkeypatch,tmp_path):
    run,state,before,ports,observed=scene(monkeypatch,tmp_path,full=False)
    run()
    assert observed[0]['protected']=={'unrelated'}


@pytest.mark.parametrize('accepted',[True,False])
def test_raw_backend_cannot_delete_closed_head_even_if_it_ignores_protected_flags(monkeypatch,tmp_path,accepted):
    run,state,before,ports,observed=scene(monkeypatch,tmp_path,accepted=accepted,change=lambda s:s['tracks'].pop('head-b'))
    with pytest.raises(ValueError):run()
    assert state==before


@pytest.mark.parametrize('retired',['missing','trimmed'])
def test_stale_closed_head_freezes_current_net_without_recapturing_metadata(monkeypatch,tmp_path,retired):
    run,state,before,ports,observed=scene(monkeypatch,tmp_path,retired=retired)
    run()
    want={'unrelated','head-a','head-b','tail-c'}|({'head-c'} if retired=='trimmed' else set())
    assert observed[0]['protected']==want
    assert observed[0]['ports']==ports


@pytest.mark.parametrize('change',[
    lambda s:s['tracks'].pop('tail-c'),
    lambda s:s['tracks']['tail-c'].update(end=[9,2]),
    lambda s:s['tracks'].update({'raw':{**s['tracks']['tail-c'],'start':[8,2],'end':[9,2]}}),
])
def test_raw_backend_cannot_reopen_or_extend_retired_source_net(monkeypatch,tmp_path,change):
    run,state,before,ports,observed=scene(monkeypatch,tmp_path,change=change)
    with pytest.raises(ValueError):run()
    assert state==before


def test_live_source_stays_exact_even_when_backend_claims_acceptance(monkeypatch,tmp_path):
    run,state,before,ports,observed=scene(monkeypatch,tmp_path,change=lambda s:s['tracks']['head-a'].update(width=200000))
    with pytest.raises(ValueError,match='source copper'):run()
    assert state==before


@pytest.mark.parametrize('change',['missing_live','moved_pad'])
def test_invalid_live_source_or_moved_physical_pad_is_not_recaptured(monkeypatch,tmp_path,change):
    from smilepcb.keyboard_routing.repair_source_scope import repair_source_scope
    run,state,before,ports,observed=scene(monkeypatch,tmp_path)
    if change=='missing_live':state['tracks'].pop('head-a')
    else:state['footprints']['chip']['pads'][1]['position']=[8,9]
    with pytest.raises(ValueError):repair_source_scope(state,ports,[['pad-b'],['pad-c']],deadline=float('inf'))


def test_source_scope_and_postcheck_cannot_renew_an_expired_deadline(monkeypatch,tmp_path):
    from smilepcb.keyboard_routing.repair_source_scope import repair_source_scope,verify_frozen_repair_nets
    run,state,before,ports,observed=scene(monkeypatch,tmp_path)
    with pytest.raises(TimeoutError):repair_source_scope(state,ports,(),deadline=-1)
    with pytest.raises(TimeoutError):verify_frozen_repair_nets(before,state,{'closed-retired'},deadline=-1)
    assert state==before


def test_added_scope_work_is_debited_from_the_same_operation_budget(monkeypatch,tmp_path):
    from smilepcb.keyboard_routing import interface_contract,repair_source_scope
    import time
    run,state,before,ports,observed=scene(monkeypatch,tmp_path)
    clock=[100.0];monkeypatch.setattr(time,'monotonic',lambda:clock[0])
    verify=interface_contract.load_verified_interfaces
    def proof(*args,**kwargs):
        result=verify(*args,**kwargs);clock[0]+=8;return result
    choose=repair_source_scope.repair_source_scope
    def scope(*args,**kwargs):
        result=choose(*args,**kwargs);clock[0]+=2;return result
    monkeypatch.setattr(interface_contract,'load_verified_interfaces',proof)
    monkeypatch.setattr(repair_source_scope,'repair_source_scope',scope)
    run()
    assert observed[0]['preflight_elapsed_s']==10
