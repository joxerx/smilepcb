"""Temporary certified source ownership, with nonphysical native boundaries.

The host cannot prove KiCad clearance. These controls execute the production
selection/install/retire bodies and catch authority or lifetime mistakes.
"""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'smilepcb/keyboard_routing/conditional_banks.py'


def definition(name, **ns):
    nodes = [n for n in ast.parse(SOURCE.read_text()).body
             if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name]
    assert len(nodes) == 1, 'Missing temporary source lifecycle: ' + name
    env = {'deepcopy': deepcopy, 'SimpleNamespace': SimpleNamespace, **ns}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(SOURCE), 'exec'), env)
    return env[name]


def port(net):
    return {'net': net, 'pad_ids': [net + '-pad'], 'added_ids': [net + '-head'],
            'terminal_id': net + '-head', 'exit_nm': [100, 200]}


def test_future_set_excludes_current_and_actual_prefix_without_assuming_order():
    banks = {'west': [port('A')], 'south': [port('B')], 'north': [port('C')]}
    heads = {n + '-head': {'net': n, 'width': 150} for n in 'ABC'}
    ports, shapes = definition('_future_heads')(banks, 'south', [{'bank': 'north'}], heads)
    assert ports == [port('A')]
    assert shapes == {'A-head': {'net': 'A', 'width': 150}}
    ports[0]['exit_nm'][0] = -1
    shapes['A-head']['width'] = -1
    assert banks['west'][0]['exit_nm'] == [100, 200]
    assert heads['A-head']['width'] == 150


def test_last_bank_has_no_temporary_sources_and_retry_reconstructs_them():
    banks = {'a': [port('A')], 'b': [port('B')]}
    heads = {n + '-head': {'net': n} for n in 'AB'}
    choose = definition('_future_heads')
    assert choose(banks, 'b', [{'bank': 'a', 'ports': [port('regenerated-A')]}], heads) == ([], {})
    assert choose(banks, 'a', [], heads) == ([port('B')], {'B-head': {'net': 'B'}})


@pytest.mark.parametrize('current,prefix', [('unknown', []), ('a', [{'bank': 'a'}]),
                                           ('b', [{'bank': 'a'}, {'bank': 'a'}]),
                                           ('a', [{'bank': 'unknown'}])])
def test_invalid_bank_history_cannot_grant_temporary_source_authority(current, prefix):
    with pytest.raises(ValueError):
        definition('_future_heads')({'a': [port('A')], 'b': [port('B')]}, current, prefix,
                                   {'A-head': {'net': 'A'}, 'B-head': {'net': 'B'}})


def test_overlapping_bank_ownership_is_rejected_before_native_emission():
    a, b = port('A'), port('B')
    b['added_ids'] = ['A-head']
    with pytest.raises(ValueError):
        definition('_future_heads')({'a': [a], 'b': [b]}, 'a', [], {'A-head': {'net': 'A'}})


class Board:
    def __init__(self):
        self.tracks = {'retained': {'net': 'fixed', 'width': 150}}
        self.footprints = {'mcu': {'pads': [{'uuid': 'future-pad', 'net': 'future', 'number': '1'}]}}
        self.raw = {'retained': 'original raw', 'future-pad': 'native pad shape'}
        self.rebuilds = 0

    def GetTracks(self):
        return [SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda u=u: u)) for u in self.tracks]

    def Delete(self, item):
        uid = item.m_Uuid.AsString()
        del self.tracks[uid]
        self.raw.pop(uid, None)

    def BuildConnectivity(self):
        self.rebuilds += 1


def lifecycle(*, conflicting_id=False, emit_error=False):
    board = Board()
    shapes = {'future-head': {'net': 'future', 'width': 150}}
    ports = [port('future')]
    inventory = deepcopy(ports)
    clock = [0.]
    def snap(b):
        return {'tracks': deepcopy(b.tracks), 'footprints': deepcopy(b.footprints)}
    def physics(b, ids):
        return {u: deepcopy(b.raw.get(u)) for u in ids}
    def all_physics(obj, excluded_ids=()):
        return {u: deepcopy(value) for u, value in obj.board.raw.items() if u not in excluded_ids}
    def emit(b, additions):
        for uid, shape in additions.items():
            b.tracks[uid] = deepcopy(shape)
            b.raw[uid] = 'native future shape'
        if emit_error:
            raise ValueError('emission failed')
    if conflicting_id:
        board.tracks['future-head'] = {'net': 'intruder'}
    cls = definition('_TemporaryHeads', snapshot=snap, _source_physics=physics,
        emit_shapes=emit, time=SimpleNamespace(monotonic=lambda: clock[0]),
        current_launches=lambda: inventory, interface_identity=lambda q: tuple(sorted(q['pad_ids'])),
        FrozenProofRouter=SimpleNamespace(_physical_state=all_physics))
    temporary = cls(board, shapes, ports,
        {'future-head': 'native future shape', 'future-pad': 'native pad shape'})
    return board, temporary, inventory, clock, snap


def test_install_has_separate_routing_baseline_and_retire_preserves_current_delta():
    board, temporary, inventory, clock, snap = lifecycle()
    parent = snap(board)
    temporary.install(deadline=10.)
    assert temporary.installed
    assert temporary.frame['tracks'] == {
        'retained': {'net': 'fixed', 'width': 150}, 'future-head': {'net': 'future', 'width': 150}}
    assert parent['tracks'] == {'retained': {'net': 'fixed', 'width': 150}}
    board.tracks['current-head'] = {'net': 'current', 'width': 150}
    board.raw['current-head'] = 'new current raw'
    assert set(board.tracks) - set(temporary.frame['tracks']) == {'current-head'}
    temporary.verify_ports()
    temporary.retire(deadline=10.)
    assert not temporary.installed
    assert board.tracks == {'retained': {'net': 'fixed', 'width': 150},
                            'current-head': {'net': 'current', 'width': 150}}
    assert board.raw == {'retained': 'original raw', 'future-pad': 'native pad shape',
                         'current-head': 'new current raw'}
    temporary.retire()  # A completed cleanup cannot delete any additional source.
    assert 'current-head' in board.tracks


@pytest.mark.parametrize('mutation', ['geometry', 'net', 'missing', 'raw', 'pad'])
def test_changed_temporary_source_rejects_retirement_without_deleting_other_copper(mutation):
    board, temporary, inventory, clock, snap = lifecycle()
    temporary.install(deadline=10.)
    if mutation == 'geometry': board.tracks['future-head']['width'] = 250
    if mutation == 'net': board.tracks['future-head']['net'] = 'wrong'
    if mutation == 'missing': del board.tracks['future-head']
    if mutation == 'raw': board.raw['future-head'] = 'changed override'
    if mutation == 'pad': board.footprints['mcu']['pads'][0]['net'] = 'wrong'
    before = snap(board)
    with pytest.raises(ValueError): temporary.retire(deadline=10.)
    assert snap(board) == before
    assert board.tracks['retained'] == {'net': 'fixed', 'width': 150}


def test_future_launch_metadata_cannot_advance_or_disappear_silently():
    board, temporary, inventory, clock, snap = lifecycle()
    temporary.install(deadline=10.)
    inventory[0]['exit_nm'] = [300, 400]
    with pytest.raises(ValueError): temporary.verify_ports()
    inventory.clear()
    with pytest.raises(ValueError): temporary.verify_ports()
    # Cleanup verifies physical authority independently after nested scope unwind.
    temporary.retire()
    assert set(board.tracks) == {'retained'}


def test_install_rejects_uuid_collision_without_changing_parent():
    board, temporary, inventory, clock, snap = lifecycle(conflicting_id=True)
    before = snap(board)
    with pytest.raises(ValueError): temporary.install(deadline=10.)
    assert snap(board) == before and not temporary.installed


def test_expired_install_and_retire_cannot_admit_but_exception_cleanup_can_finish():
    board, temporary, inventory, clock, snap = lifecycle()
    clock[0] = 11.
    with pytest.raises(TimeoutError): temporary.install(deadline=10.)
    assert set(board.tracks) == {'retained'}
    clock[0] = 0.
    temporary.install(deadline=10.)
    clock[0] = 11.
    with pytest.raises(TimeoutError): temporary.retire(deadline=10.)
    assert 'future-head' in board.tracks
    temporary.retire()
    assert set(board.tracks) == {'retained'}


def test_emission_exception_still_leaves_exact_cleanup_authority():
    board, temporary, inventory, clock, snap = lifecycle(emit_error=True)
    before = snap(board)
    with pytest.raises(ValueError, match='emission failed'):
        temporary.install(deadline=10.)
    temporary.retire()
    assert snap(board) == before and not temporary.installed


def test_retirement_rejects_a_native_delete_that_alters_remaining_physics():
    board, temporary, inventory, clock, snap = lifecycle()
    temporary.install(deadline=10.)
    delete = board.Delete
    def changed_delete(item):
        delete(item)
        board.raw['retained'] = 'unexpected native mutation'
    board.Delete = changed_delete
    with pytest.raises(ValueError, match='retained native geometry'):
        temporary.retire(deadline=10.)
