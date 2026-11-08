"""Accepted repairs can continue without widening old creator authority."""
from copy import deepcopy
from types import SimpleNamespace as S
import sys
import pytest
from smilepcb.keyboard_routing import feeder_repair as repair
from smilepcb.keyboard_routing.generation import generation_profile
from test_transient_generated_access import kernel


def track(net):
    return dict(net=net,locked=False,width=150000,layer=0,**{'class':'PCB_TRACK'})


def setup_owner(monkeypatch):
    api=kernel()
    state=dict(tracks={'hard':track('matrix'), 'feeder':track('matrix'),
        'old-soft':track('supply'), 'unchanged-soft':track('return')},
        footprints={'chip':dict(pads=[dict(uuid='pad',net='supply',number='1')])})
    owner=api.ProvisionalOwnership('dsn-process-v2',
        {u:deepcopy(state['tracks'][u]) for u in ('old-soft','unchanged-soft')},
        {'pad':deepcopy(state['footprints']['chip']['pads'][0])})
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.collector_preparation',api)
    return api,state,owner


def test_contraction_only_retains_exact_old_objects(monkeypatch):
    api,state,owner=setup_owner(monkeypatch)
    state['tracks'].pop('old-soft');state['tracks']['new-return']=track('supply')
    with generation_profile('dsn-process-v2'):
        narrowed=api.contract_provisional_ownership(state,owner,{'matrix'})
        assert api.transient_access_ids(state,narrowed,{'matrix'})=={'unchanged-soft'}
    assert set(owner.tracks)=={'old-soft','unchanged-soft'}


@pytest.mark.parametrize('mutation',['pad','profile','matrix','locked'])
def test_contraction_does_not_refresh_invalid_witnesses(monkeypatch,mutation):
    api,state,owner=setup_owner(monkeypatch)
    if mutation=='pad':state['footprints']['chip']['pads'][0]['net']='wrong'
    elif mutation=='profile':owner=api.ProvisionalOwnership('legacy',owner.tracks,owner.pads)
    elif mutation=='matrix':owner.tracks['old-soft']['net']='matrix'
    else:owner.tracks['old-soft']['locked']=True
    with generation_profile('dsn-process-v2'),pytest.raises(ValueError):
        api.contract_provisional_ownership(state,owner,{'matrix'})


def test_epoch_protects_new_nonmatrix_and_only_releases_traced_matrix(monkeypatch):
    api,before,owner=setup_owner(monkeypatch);after=deepcopy(before)
    after['tracks'].pop('old-soft');after['tracks'].pop('feeder')
    after['tracks'].update(new_feeder=track('matrix'),new_return=track('supply'))
    with generation_profile('dsn-process-v2'):
        narrowed,protected=repair._advance_participant_epoch(before,after,owner,{'matrix'},
            {'hard'},['new_feeder','new_return'])
        assert api.transient_access_ids(after,narrowed,{'matrix'})=={'unchanged-soft'}
    assert protected=={'hard','new_return'}


@pytest.mark.parametrize('emitted',[None,[],['new_feeder','new_feeder'],['new_feeder','hard']])
def test_epoch_stops_without_exact_creation_inventory(monkeypatch,emitted):
    _,before,owner=setup_owner(monkeypatch);after=deepcopy(before)
    after['tracks']['new_feeder']=track('matrix')
    with generation_profile('dsn-process-v2'),pytest.raises(ValueError):
        repair._advance_participant_epoch(before,after,owner,{'matrix'},{'hard'},emitted)


def test_scheduler_continues_from_actual_global_connectivity(monkeypatch):
    api,state,owner=setup_owner(monkeypatch);state['tracks']['feeder']['net']='first'
    state['tracks']['hard']['net']='first';pending={'first':1,'second':1};calls=[]
    monkeypatch.setattr(repair,'time',S(monotonic=lambda:0.))
    monkeypatch.setattr(repair,'pending_matrix',lambda *a:dict(pending))
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',S(snapshot=lambda b:deepcopy(state)))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.interface_launches',S(current_launches=lambda:[],interface_identity=lambda p:tuple(p['pad_ids'])))
    def inner(board,nets,protected,**kw):
        targets=set(nets)&pending.keys();calls.append(set(targets))
        if len(targets)>1:return [dict(accepted=False,before=dict(pending),after=dict(pending),retained_pending=dict(pending))]
        target=next(iter(targets))
        if target=='first':
            state['tracks'].pop('old-soft');state['tracks']['new_return']=track('supply')
            state['tracks']['new_feeder']=track('first');emitted=['new_return','new_feeder']
        else:
            assert api.transient_access_ids(state,kw['participant_ownership'],{'first','second'})=={'unchanged-soft'}
            assert 'new_return' in protected and 'new_feeder' not in protected and 'hard' in protected
            emitted=[]
        pending.pop(target)
        return [dict(accepted=True,before={target:1},after={},actual_emitted_ids=emitted)]
    monkeypatch.setattr(repair,'_repair_feeders',inner)
    with generation_profile('dsn-process-v2'):
        rows=repair.repair_feeders(None,{'first','second'},{'hard'},deadline=300,participant_ownership=owner)
    assert pending=={} and rows[-1]['after']=={}
    assert calls==[{'first','second'},{'first'},{'second'}]
    assert rows[1]['participant_epoch_advanced'] is True


@pytest.mark.parametrize('mutation',['hard','pad'])
def test_epoch_rejects_geometry_or_binding_changes_to_hard_objects(monkeypatch,mutation):
    _,before,owner=setup_owner(monkeypatch);after=deepcopy(before)
    if mutation=='hard':after['tracks']['hard']['width']+=1
    else:after['footprints']['chip']['pads'][0]['net']='wrong'
    with generation_profile('dsn-process-v2'),pytest.raises(ValueError):
        repair._advance_participant_epoch(before,after,owner,{'matrix'},{'hard'},[])


def test_existing_hard_participant_stays_hard_after_epoch(monkeypatch):
    _,before,owner=setup_owner(monkeypatch)
    with generation_profile('dsn-process-v2'):
        _,protected=repair._advance_participant_epoch(before,deepcopy(before),owner,
            {'matrix'},{'hard','unchanged-soft'},[])
    assert 'unchanged-soft' in protected



def test_scheduler_does_not_continue_on_a_scope_only_success(monkeypatch):
    _,state,owner=setup_owner(monkeypatch);pending={'first':1,'second':1};calls=[]
    monkeypatch.setattr(repair,'time',S(monotonic=lambda:0.))
    monkeypatch.setattr(repair,'pending_matrix',lambda *a:dict(pending))
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',S(snapshot=lambda b:deepcopy(state)))
    monkeypatch.setitem(sys.modules,'smilepcb.keyboard_routing.interface_launches',S(current_launches=lambda:[],interface_identity=lambda p:tuple(p['pad_ids'])))
    def inner(board,nets,protected,**kw):
        calls.append(set(nets))
        if len(calls)==1:return [dict(accepted=False,before=dict(pending),after=dict(pending),retained_pending=dict(pending))]
        # A scope report cannot hide the unchanged actual global measurement.
        return [dict(accepted=True,before={'first':1},after={},actual_emitted_ids=[])]
    monkeypatch.setattr(repair,'_repair_feeders',inner)
    with generation_profile('dsn-process-v2'):
        rows=repair.repair_feeders(None,{'first','second','matrix'},set(),deadline=300,participant_ownership=owner)
    assert len(calls)==2 and rows[-1]['participant_epoch_ended']
    assert 'global matrix progress' in rows[-1]['participant_epoch_reason']
