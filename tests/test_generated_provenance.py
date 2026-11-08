"""Positive ownership must survive copies without claiming old unknown copper."""
import json
import shutil
import pytest
from smilepcb.keyboard_routing import generated_provenance as gp

U='00000000-0000-0000-0000-000000000001'
G='00000000-0000-0000-0000-000000000002'
N='00000000-0000-0000-0000-000000000003'

def write_board(path, ids, *, moved=(), net='SIG', zones=False):
    rows=['(kicad_pcb (version 20241229) (generator test) (net 1 "'+net+'")']
    for uid in ids:
        x=7 if uid in moved else 0
        rows.append(f'(segment (start {x} 0) (end 1 0) (width 0.15) (layer "F.Cu") (net 1) (uuid {uid}))')
    if zones:rows.append('(zone (net 1) (net_name "SIG"))')
    path.write_text('\n'.join(rows)+')\n')

def setup_board(tmp_path):
    board=tmp_path/'keyboard.kicad_pcb';write_board(board,[U])
    before=gp.inventory(board)
    write_board(board,[U,G])
    gp.record_creation(board,before,creator='controlled-placement',imported_ids=[U])
    return board

def test_creation_delta_records_only_new_copper_and_preserves_import_class(tmp_path):
    board=setup_board(tmp_path)
    assert gp.generated_ids(board)==[G]
    ledger=json.loads((tmp_path/gp.MARKER).read_text())
    assert set(ledger['imported'])=={U}
    assert ledger['generated'][G]['creator']=='controlled-placement'
    copy=tmp_path/'copy';shutil.copytree(tmp_path,copy,ignore=shutil.ignore_patterns('copy'))
    assert gp.generated_ids(copy/board.name)==[G]

def test_claimed_added_ids_do_not_claim_preexisting_unknown_copper(tmp_path):
    board=setup_board(tmp_path)
    def worker(script,request,log,**kwargs):
        write_board(board,[U,G,N]);return {'accepted':True,'added_ids':[U,N]}
    gp.tracked_worker(worker,'native',{'operation':'components','board':str(board)},tmp_path/'run.log')
    assert gp.generated_ids(board)==[G,N]

def test_stale_shape_and_forged_binding_fail_before_worker(tmp_path):
    board=setup_board(tmp_path);write_board(board,[U,G],moved=[G])
    with pytest.raises(ValueError,match='binding'):gp.generated_ids(board)
    with pytest.raises(ValueError,match='binding'):
        gp.tracked_worker(lambda *a,**k:pytest.fail('stale ledger invoked worker'),'native',{'operation':'components','board':str(board)},tmp_path/'log')

@pytest.mark.parametrize('protected,changed', [([],U),([G],G)])
def test_unknown_or_explicit_protected_mutation_rolls_back_all_receipts(tmp_path,protected,changed):
    board=setup_board(tmp_path);field=tmp_path/'.smilepcb-field.json';field.write_text('old field')
    before={p.name:p.read_bytes() for p in (board,field,tmp_path/gp.MARKER)}
    def worker(script,request,log,**kwargs):
        assert U in request['protected_ids']
        write_board(board,[U,G],moved=[changed]);field.write_text('changed field');return {'accepted':True}
    with pytest.raises(ValueError,match='protected'):
        gp.tracked_worker(worker,'native',{'operation':'joint_repair','board':str(board),'protected_ids':protected},tmp_path/'log')
    assert {p.name:p.read_bytes() for p in (board,field,tmp_path/gp.MARKER)}==before

def test_rejected_branch_cannot_publish_new_ownership(tmp_path):
    board=setup_board(tmp_path);before=board.read_bytes();ledger=(tmp_path/gp.MARKER).read_bytes()
    def worker(*args,**kwargs):write_board(board,[U,G,N]);return {'accepted':False}
    with pytest.raises(ValueError,match='Rejected'):
        gp.tracked_worker(worker,'native',{'operation':'complete','board':str(board)},tmp_path/'log')
    assert board.read_bytes()==before and (tmp_path/gp.MARKER).read_bytes()==ledger

def test_accepted_retirement_and_replacement_uses_saved_new_identity(tmp_path):
    board=setup_board(tmp_path)
    def worker(*args,**kwargs):write_board(board,[U,N]);return {'accepted':True}
    gp.tracked_worker(worker,'native',{'operation':'joint_repair','board':str(board)},tmp_path/'log')
    assert gp.generated_ids(board)==[N]
    write_board(board,[U,G,N])
    with pytest.raises(ValueError,match='retired'):gp.generated_ids(board)

def test_missing_ledger_does_not_bootstrap_unknown_from_protection(tmp_path):
    board=tmp_path/'keyboard.kicad_pcb';write_board(board,[U,G]);assert gp.generated_ids(board)==[]
    def worker(script,request,log,**kwargs):
        assert set(request['protected_ids'])=={U,G}
        write_board(board,[U,G,N]);return {'added_ids':[U,G,N]}
    gp.tracked_worker(worker,'native',{'operation':'components','board':str(board)},tmp_path/'log')
    assert gp.generated_ids(board)==[N]

def test_compose_allows_only_declared_net_rebinding(tmp_path):
    board=setup_board(tmp_path);before=gp.inventory(board);write_board(board,[U,G],net='NEW')
    gp.rebind_after_compose(board,before,net_remap={'SIG':'NEW'});assert gp.generated_ids(board)==[G]
    before=gp.inventory(board);original=(tmp_path/gp.MARKER).read_bytes();write_board(board,[U,G],net='OTHER',moved=[G])
    with pytest.raises(ValueError,match='geometry'):gp.rebind_after_compose(board,before,net_remap={'NEW':'OTHER'})
    assert (tmp_path/gp.MARKER).read_bytes()==original

def test_rule_area_is_not_a_filled_plane_boundary(tmp_path):
    board=setup_board(tmp_path)
    board.write_text(board.read_text()[:-2]+'(zone (net 0) (keepout (tracks not_allowed))) )\n')
    assert not gp.has_zones(board)
    write_board(board,[U,G],zones=True)
    assert gp.has_zones(board)


def test_failed_profile_receipt_rolls_back_created_copper(tmp_path):
    board=setup_board(tmp_path);before=board.read_bytes()
    def worker(*args,**kwargs):write_board(board,[U,G,N]);return {'accepted':True,'generation_profile':'wrong'}
    with pytest.raises(ValueError,match='profile'):
        gp.tracked_worker(worker,'native',{'operation':'components','board':str(board),'generation_profile':'dsn-process-v2'},tmp_path/'log')
    assert board.read_bytes()==before and gp.generated_ids(board)==[G]

def test_read_only_worker_cannot_mint_ownership(tmp_path):
    board=setup_board(tmp_path);before=board.read_bytes()
    def worker(*args,**kwargs):write_board(board,[U,G,N]);return {'accepted':True}
    with pytest.raises(ValueError,match='Read-only'):
        gp.tracked_worker(worker,'native',{'operation':'verify','board':str(board)},tmp_path/'log')
    assert board.read_bytes()==before and gp.generated_ids(board)==[G]


def test_unknown_worker_operation_cannot_mint_ownership(tmp_path):
    board=setup_board(tmp_path)
    with pytest.raises(ValueError,match='controlled creation'):
        gp.tracked_worker(lambda *a,**k:pytest.fail('unknown operation invoked'),'native',{'operation':'untrusted-receipt','board':str(board)},tmp_path/'log')

@pytest.mark.parametrize('text',['not-a-board','(garbage)','()'])
def test_malformed_board_cannot_seed_a_creation_receipt(tmp_path,text):
    board=tmp_path/'keyboard.kicad_pcb';board.write_text(text)
    with pytest.raises(ValueError,match='board'):
        gp.record_creation(board,{},creator='controlled-placement')
    assert not (tmp_path/gp.MARKER).exists()

@pytest.mark.parametrize('operation,marker',[
    ('verify','.smilepcb-field.json'),
    ('verify','.smilepcb-interfaces.json'),
    ('controller_ports','.smilepcb-interfaces.json'),
    ('controller_ports','.smilepcb-field.json'),
    ('joint_repair','.smilepcb-interfaces.json'),
    ('components',gp.MARKER),
])
@pytest.mark.parametrize('present',[False,True])
def test_successful_worker_cannot_publish_unauthorized_policy_receipt(tmp_path,operation,marker,present):
    board=setup_board(tmp_path)
    selected=tmp_path/marker
    if not present:selected.unlink(missing_ok=True)
    elif marker!=gp.MARKER:selected.write_text('original policy')
    paths=[board,tmp_path/gp.MARKER,tmp_path/'.smilepcb-field.json',tmp_path/'.smilepcb-interfaces.json']
    before={path:path.read_bytes() if path.exists() else None for path in paths}
    def worker(*args,**kwargs):
        selected.write_text('unauthorized policy')
        return {'accepted':True}
    with pytest.raises(ValueError,match='immutable receipt'):
        gp.tracked_worker(worker,'native',{'operation':operation,'board':str(board)},tmp_path/'log')
    assert {path:path.read_bytes() if path.exists() else None for path in paths}==before


@pytest.mark.parametrize('operation',['matrix_field','joint_repair'])
def test_explicit_native_field_publisher_can_publish_its_checked_receipt(tmp_path,operation):
    board=setup_board(tmp_path);field=tmp_path/'.smilepcb-field.json'
    def worker(*args,**kwargs):
        field.write_text('native checked field transition')
        return {'accepted':True}
    gp.tracked_worker(worker,'native',{'operation':operation,'board':str(board)},tmp_path/'log')
    assert field.read_text()=='native checked field transition'
    assert gp.generated_ids(board)==[G]


def test_fresh_compiled_import_marks_only_positive_power_generated(tmp_path):
    board=tmp_path/'keyboard.kicad_pcb';write_board(board,[U]);before=gp.inventory(board)
    write_board(board,[U,G,N])
    gp.record_import_creation(board,before,creator='compiled-controller',immutable_ids={G},generated_ids={N})
    assert gp.generated_ids(board)==[N]
    baseline=gp.inventory(board);write_board(board,[U,G],moved=[])
    gp.record_creation(board,baseline,creator='accepted-placement',imported_ids={G})
    assert gp.generated_ids(board)==[]
    assert json.loads((tmp_path/gp.MARKER).read_text())['retired_ids']==[N]


@pytest.mark.parametrize('immutable,generated', [({G},{G,N}),({G},set()),({G},{U,N})])
def test_fresh_import_requires_exact_disjoint_observed_delta(tmp_path,immutable,generated):
    board=tmp_path/'keyboard.kicad_pcb';write_board(board,[U]);before=gp.inventory(board)
    write_board(board,[U,G,N])
    with pytest.raises(ValueError,match='import'):
        gp.record_import_creation(board,before,creator='compiled-controller',immutable_ids=immutable,generated_ids=generated)
    assert not (tmp_path/gp.MARKER).exists()


def test_fresh_import_does_not_authorize_old_copper_changes(tmp_path):
    board=tmp_path/'keyboard.kicad_pcb';write_board(board,[U]);before=gp.inventory(board)
    write_board(board,[U,G,N],moved=[U])
    with pytest.raises(ValueError,match='protected'):
        gp.record_import_creation(board,before,creator='compiled-controller',immutable_ids={G},generated_ids={N})
    assert not (tmp_path/gp.MARKER).exists()


def test_fresh_import_keeps_raw_module_copper_immutable(tmp_path):
    board=tmp_path/'keyboard.kicad_pcb';write_board(board,[]);before=gp.inventory(board)
    write_board(board,[G,N])
    gp.record_import_creation(board,before,creator='compiled-controller',immutable_ids={G},generated_ids={N})
    baseline=gp.inventory(board);write_board(board,[G,N],moved=[G])
    with pytest.raises(ValueError,match='protected'):
        gp.record_creation(board,baseline,creator='accepted-placement')


@pytest.mark.parametrize('malicious',[False,True])
def test_reported_tail_retirement_has_exact_independent_receipt_check(tmp_path,malicious):
    board=setup_board(tmp_path);field=tmp_path/'.smilepcb-field.json'
    receipt={'schema':1,'roles':{U:'column_trunk'},'tracks':{U:{}},'prerequisite_tracks':{G:{}},'terminals':{'pad':['columns','SIG']}}
    field.write_text(json.dumps(receipt));paths=[board,field,tmp_path/gp.MARKER];before={p:p.read_bytes() for p in paths}
    findings=[{'type':'track_dangling','severity':'warning','items':[{'uuid':G}]}]
    def worker(script,request,log,**kwargs):
        assert request['generated_ids']==[G]
        write_board(board,[U]);updated=json.loads(json.dumps(receipt));updated['prerequisite_tracks'].pop(G)
        if malicious:updated['roles'][U]='row_trunk'
        field.write_text(json.dumps(updated));return {'removed_ids':[G],'pad_partition_preserved':True}
    if malicious:
        with pytest.raises(ValueError):gp.tracked_worker(worker,'native',{'operation':'prune_reported_tails','board':str(board),'findings':findings},tmp_path/'log')
        assert {p:p.read_bytes() for p in paths}==before
    else:
        gp.tracked_worker(worker,'native',{'operation':'prune_reported_tails','board':str(board),'findings':findings},tmp_path/'log')
        assert gp.generated_ids(board)==[] and json.loads(field.read_text())['roles']==receipt['roles']


def test_reported_tail_cannot_retire_even_a_resolved_controller_source(tmp_path):
    board=setup_board(tmp_path);field=tmp_path/'.smilepcb-field.json';interface=tmp_path/'.smilepcb-interfaces.json'
    field.write_text(json.dumps({'schema':1,'roles':{U:'column_trunk'},'prerequisite_tracks':{G:{}}}))
    interface.write_text(json.dumps({'tracks':{G:{}},'ports':[{'net':'already-closed'}]}))
    findings=[{'type':'track_dangling','severity':'warning','items':[{'uuid':G}]}]
    original={p:p.read_bytes() for p in (board,field,interface,tmp_path/gp.MARKER)}
    with pytest.raises(ValueError):
        gp.tracked_worker(lambda *a,**k:pytest.fail('Source removal must be rejected before worker'),
            'native',{'operation':'prune_reported_tails','board':str(board),'findings':findings},tmp_path/'log')
    assert {p:p.read_bytes() for p in original}==original
