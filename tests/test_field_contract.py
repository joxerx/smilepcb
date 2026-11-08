"""A later pass may add copper, but cannot silently discard matrix spines."""
from copy import deepcopy
import pytest
from smilepcb.keyboard_routing import field_contract as fc


def fixture():
    state={'tracks':{'trunk':{'class':'PCB_TRACK','start':[0,0],'end':[0,10],
        'width':150000,'layer':0,'net':'column','locked':False}},
        'footprints':{'switch':{'pads':[{'uuid':'pad','net':'column'}]}}}
    report={'complete':True,'roles':{'trunk':'column_trunk'},'owned_ids':['trunk'],
            'terminals':{'pad':['columns','column']}}
    return state,report


def test_capture_requires_a_complete_native_field():
    state,report=fixture();report['complete']=False
    with pytest.raises(ValueError):fc.capture(state,report)


def test_added_copper_does_not_invalidate_existing_spines():
    state,report=fixture();contract=fc.capture(state,report)
    state['tracks']['new']={**state['tracks']['trunk'],'net':'other'}
    assert fc.validate(state,contract)==['trunk']


@pytest.mark.parametrize('change',['drop','geometry','net','pad','role','ownership'])
def test_changed_spine_or_terminal_binding_is_rejected(change):
    state,report=fixture();contract=fc.capture(state,report)
    if change=='drop':state['tracks'].clear()
    if change=='geometry':state['tracks']['trunk']['end']=[2,10]
    if change=='net':state['tracks']['trunk']['net']='other'
    if change=='pad':state['footprints']['switch']['pads'][0]['net']='other'
    if change=='role':contract['roles']['trunk']='unknown'
    if change=='ownership':contract['tracks']['unowned']=state['tracks']['trunk']
    with pytest.raises(ValueError):fc.validate(state,contract)


def test_capture_is_not_an_alias_of_mutable_native_state():
    state,report=fixture();contract=fc.capture(state,report)
    state['tracks']['trunk']['end'][1]=20
    with pytest.raises(ValueError):fc.validate(state,contract)


def test_constructed_access_cannot_be_dropped_by_external_rerouting():
    state,report=fixture();state['tracks']['access']={**state['tracks']['trunk'],'start':[0,10],'end':[4,10]}
    report['roles']['access']='column_access';report['owned_ids'].append('access')
    contract=fc.capture(state,report)
    assert set(fc.validate(state,contract))=={'trunk','access'}
    del state['tracks']['access']
    with pytest.raises(ValueError):fc.validate(state,contract)


def composition_fixture():
    state,report=fixture();state['footprints']['switch'].update(ref='SW1',path='/old',position=[0,0])
    return state,fc.capture(state,report)


def test_composition_rebinds_only_declared_paths_and_exported_net_names():
    state,contract=composition_fixture();after=deepcopy(state)
    after['footprints']['switch']['path']='/sheet/symbol'
    after['footprints']['switch']['pads'][0]['net']='exported'
    after['tracks']['trunk']['net']='exported'
    updated=fc.rebind_after_compose(state,after,contract,net_remap={'column':'exported'},paths={'SW1':'/sheet/symbol'})
    assert fc.validate(after,updated)==['trunk']
    assert updated['terminals']=={'pad':['columns','exported']}
    assert contract['terminals']=={'pad':['columns','column']}


@pytest.mark.parametrize('change',['track','position','pad','extra','undeclared_path'])
def test_composition_cannot_launder_changed_geometry_into_a_new_receipt(change):
    state,contract=composition_fixture();after=deepcopy(state)
    after['footprints']['switch']['path']='/sheet/symbol'
    if change=='track':after['tracks']['trunk']['end']=[0,11]
    if change=='position':after['footprints']['switch']['position']=[1,0]
    if change=='pad':after['footprints']['switch']['pads'][0]['net']='other'
    if change=='extra':after['tracks']['extra']=deepcopy(after['tracks']['trunk'])
    if change=='undeclared_path':after['footprints']['switch']['path']='/unexpected'
    with pytest.raises(ValueError):fc.rebind_after_compose(state,after,contract,net_remap={},paths={'SW1':'/sheet/symbol'})


def test_prerequisite_copper_is_protected_without_matrix_role():
    state,report=fixture();state['tracks']['rgb-access']={**state['tracks']['trunk'],'net':'rgb'}
    contract=fc.capture(state,report,prerequisite_ids=['rgb-access'])
    assert set(fc.validate(state,contract))=={'trunk','rgb-access'}
    assert 'rgb-access' not in contract['roles']
    del state['tracks']['rgb-access']
    with pytest.raises(ValueError):fc.validate(state,contract)


def test_compose_rebinds_protected_prerequisites_as_well_as_matrix():
    state,report=fixture();state['footprints']['switch'].update(ref='SW1',path='/old')
    state['tracks']['access']={**state['tracks']['trunk'],'net':'rgb'}
    contract=fc.capture(state,report,prerequisite_ids=['access'])
    after=deepcopy(state);after['footprints']['switch']['path']='/new'
    after['tracks']['access']['net']='rgb-exported'
    updated=fc.rebind_after_compose(state,after,contract,net_remap={'rgb':'rgb-exported'},paths={'SW1':'/new'})
    assert set(fc.validate(after,updated))=={'trunk','access'}


@pytest.mark.parametrize('name',['native','dsn-safe-v1','dsn-process-v2'])
def test_process_profile_is_bound_to_actual_field_creation_context(name):
    from smilepcb.keyboard_routing.generation import generation_profile
    state,report=fixture()
    with generation_profile(name):contract=fc.capture(state,report)
    assert contract['generation_profile']==name
    assert fc.validate(state,contract)==['trunk']


def test_reported_prerequisite_cannot_retire_resolved_source_on_native_boundary(tmp_path,monkeypatch):
    import json,sys
    from types import SimpleNamespace as S
    state,report=fixture();state['tracks']['tail']={**state['tracks']['trunk'],'net':'supply'}
    contract=fc.capture(state,report,prerequisite_ids=['tail'])
    path=tmp_path/'keyboard.kicad_pcb';path.write_text('saved board')
    (tmp_path/fc.MARKER).write_text(json.dumps(contract))
    (tmp_path/'.smilepcb-interfaces.json').write_text(json.dumps({'tracks':{'tail':{}},'ports':[{'net':'closed'}]}))
    monkeypatch.setitem(sys.modules,'smilepcb.native_worker',S(p=S(LoadBoard=lambda p:state),snapshot=lambda board:board))
    monkeypatch.setattr(fc,'verify_native',lambda *a,**k:['trunk','tail'])
    request={'operation':'prune_reported_tails','board':str(path),'generated_ids':['tail'],
        'findings':[{'type':'track_dangling','severity':'warning','items':[{'uuid':'tail'}]}]}
    with pytest.raises(ValueError):fc.guarded(request,lambda *a:pytest.fail('Source pruning must not run'))
    assert path.read_text()=='saved board' and json.loads((tmp_path/fc.MARKER).read_text())==contract
