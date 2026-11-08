"""Run the real mixed trial transaction; only native CAD boundaries are doubles."""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from smilepcb.keyboard_routing.conditional_sources import SourceSearchBudget, geometry_signature
from test_future_head_caller import launch_module, copper, port

SOURCE = Path(__file__).parents[1] / 'smilepcb/keyboard_routing/conditional_banks.py'


class Board:
    def __init__(self, tracks):
        self.tracks = deepcopy(tracks)
        self.physics = {u: 'exact native properties' for u in tracks}
        self.builds = 0
    def GetTracks(self):
        return [SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda u=u: u)) for u in self.tracks]
    def Delete(self, item):
        uid = item.m_Uuid.AsString()
        del self.tracks[uid]
        del self.physics[uid]
    def BuildConnectivity(self):
        self.builds += 1
    def GetDesignSettings(self):
        return SimpleNamespace(m_TrackMinWidth=150000, m_MinClearance=173000)


def fixture(*, scenario='second', count=3, clock_step=0.):
    launches = launch_module()
    ports = [port('signal-' + str(i), 'old-' + str(i)) for i in range(count)]
    ports[0]['pad_ids'].append('alias-first')
    heads = {q['added_ids'][0]: copper(q['net'], 100*i) for i,q in enumerate(ports)}
    fixed = port('fixed', 'fixed-head')
    future = port('future', 'future-head')
    board = Board({'fixed-head': copper('fixed', -100), 'future-head': copper('future', -200)})
    original = Board(board.tracks | heads)
    baseline = deepcopy(board.tracks)
    base_physics = deepcopy(board.physics)
    events = []
    clock = SimpleNamespace(now=0.)
    def monotonic():
        now = clock.now
        clock.now += clock_step
        return now
    def snapshot(actual):
        return {'tracks': deepcopy(actual.tracks), 'footprints': {'pads': 'immutable'}}
    def physics(actual, ids):
        return {u: actual.physics.get(u, 'pad properties') for u in sorted(ids)}
    def seal(actual, excluded_ids=()):
        return {u: v for u,v in actual.board.physics.items() if u not in excluded_ids}
    def emit(actual, shapes):
        assert not shapes.keys() & actual.tracks.keys()
        actual.tracks.update(deepcopy(shapes))
        actual.physics.update({u: 'exact native properties' for u in shapes})
    class Router:
        def __init__(self, actual, *, deadline, **kwargs):
            assert actual is board and deadline == 10.
            self.frame = snapshot(board)
            events.append(('router', set(board.tracks), {q['net'] for q in launches.current_launches()}))
        def refresh_interfaces(self, graph):
            assert graph['frame'] == self.frame
            launches.current_launch_inventory().resolved.add(('temporary-resolution',))
    def graph(actual, nets, deadline):
        assert actual is board
        ids = {u for q in ports + [fixed, future] for u in q['pad_ids']} | set(board.tracks)
        ordered = sorted(ids)
        return {'frame': snapshot(actual), 'nodes': [
            {'item': SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda u=u: u))}
            for u in ordered], 'find': lambda i: 1 if ordered[i] == 'orphan' else 0}
    def admit(router, shapes, actual_ports, *, deadline):
        assert router.frame == snapshot(board)
        assert not shapes.keys() & board.tracks.keys(), 'Absent-head oracle would collide with its own via hole'
        events.append(('admit', {q['net'] for q in actual_ports}, {q['net'] for q in launches.current_launches()}))
        return {'accepted': True, 'failed_interfaces': []}
    def saved_proof(actual, shapes, g, covered, deadline):
        assert actual is board and g['frame'] == snapshot(board)
        assert shapes.keys() <= board.tracks.keys()
        own, = {q['net'] for q in shapes.values()}
        scope = {q['net'] for q in launches.current_launches()}
        assert own not in scope, 'Source interiors must not be judged as new feeder first contact'
        assert scope == {'fixed', 'future'} | ({q['net'] for q in ports} - {own})
        events.append(('saved-proof', own, scope))
        if scenario == 'reverse-guard':
            raise ValueError('Conditional matrix via violates creation oracle')
        return len(shapes)
    calls = []
    def generate(actual, **kwargs):
        assert actual is board
        target_net, = kwargs['include_nets']
        target = next(q for q in ports if q['net'] == target_net)
        calls.append((target_net, kwargs))
        assert kwargs['deadline'] == 10.
        assert isinstance(kwargs['source_budget'], SourceSearchBudget)
        assert all(call[1]['source_budget'] is kwargs['source_budget'] for call in calls)
        assert {q['net'] for q in launches.current_launches()} == {'fixed', 'future'} | ({q['net'] for q in ports} - {target_net})
        assert not set(target['added_ids']) & set(board.tracks)
        retained = {u: heads[u] for q in ports if q is not target for u in q['added_ids']}
        assert board.tracks == baseline | retained
        assert kwargs['head_constraint'] == {
            'original_geometry': geometry_signature({u: heads[u] for u in target['added_ids']}),
            'unchanged_heads': {tuple(sorted(target['pad_ids'])):
                                geometry_signature({u: heads[u] for u in target['added_ids']})}}
        if scenario == 'second' and len(calls) == 1 or scenario == 'all-empty':
            return {'accepted': False, 'reason': 'No legal outward interface candidates'}
        if scenario == 'expired':
            clock.now = 10.
        uid = 'new-' + target_net
        emit(board, {uid: copper(target_net, 2000 + len(calls))})
        if scenario == 'fatal':
            raise RuntimeError('unexpected native fault')
        if scenario == 'mutate-retained':
            board.physics[next(iter(retained))] = 'mutated'
        answer = deepcopy(target)
        answer.update(added_ids=[uid], terminal_id=uid)
        if scenario == 'orphan-owned':
            emit(board, {'orphan': copper(target_net, 9000)})
            answer['added_ids'].append('orphan')
        if scenario == 'empty-port':
            return {'accepted': True, 'ports': []}
        if scenario == 'partial-alias':
            answer['pad_ids'] = answer['pad_ids'][:1]
        if scenario == 'same-geometry':
            board.tracks[uid] = deepcopy(heads[target['added_ids'][0]])
        return {'accepted': True, 'ports': [answer]}
    shared = SourceSearchBudget([q['pad_ids'] for q in ports])
    env = {'deepcopy': deepcopy, 'time': SimpleNamespace(monotonic=monotonic),
           'snapshot': snapshot, '_source_physics': physics, 'geometry_signature': geometry_signature,
           'current_launches': launches.current_launches, 'reserve_launches': launches.reserve_launches,
           'interface_identity': launches.interface_identity, 'LocalRouter': Router,
           'copper_graph': graph, '_admit_heads': admit, 'emit_shapes': emit,
           '_prove_copper': saved_proof,
           'FrozenProofRouter': SimpleNamespace(_physical_state=seal), 'SimpleNamespace': SimpleNamespace}
    nodes = [n for n in ast.parse(SOURCE.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == '_mixed_source_heads']
    assert nodes, 'Production mixed-source transaction is absent'
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(SOURCE), 'exec'), env)
    def run(feedback=None):
        with launches.reserve_launches([fixed, future]):
            inventory = launches.current_launch_inventory()
            inventory.resolved = {('pad-fixed',)}
            inventory.repair_reserved = {('pad-future',)}
            try:
                return env['_mixed_source_heads'](board, ports, heads,
                    feedback if feedback is not None else [tuple(q['pad_ids']) for q in ports],
                    generate=generate, arguments={}, deadline=10., source_budget=shared,
                    source_board=original)
            finally:
                assert launches.current_launches() == (fixed, future)
                assert launches.current_launch_inventory() is inventory
                assert inventory.resolved == {('pad-fixed',)}
                assert inventory.repair_reserved == {('pad-future',)}
    return SimpleNamespace(run=run, board=board, baseline=baseline, base_physics=base_physics,
                           ports=ports, heads=heads, calls=calls, events=events, clock=clock,
                           env=env, generate=generate, original=original, launches=launches,
                           outer=[fixed, future])


def test_original_only_target_does_not_block_viable_failed_neighbor():
    f = fixture()
    result = f.run()
    assert result['accepted'] and result['strategy'] == 'mixed_single_interface'
    assert result['selected_interface'] == f.ports[1]['pad_ids']
    assert [q[0] for q in f.calls] == ['signal-0', 'signal-1']
    assert result['ports'][0] == f.ports[0] and result['ports'][2] == f.ports[2]
    assert f.board.tracks['old-0'] == f.heads['old-0']
    assert 'old-1' not in f.board.tracks
    full = [q for q in f.events if q[0] == 'saved-proof']
    assert [q[1] for q in full] == ['signal-0', 'signal-1', 'signal-2']
    assert 'signal-1' in full[0][2] and 'signal-0' in full[1][2]
    assert f.board.builds >= 1


@pytest.mark.parametrize('scenario', ['all-empty', 'reverse-guard', 'expired'])
def test_failed_trials_restore_exact_native_frame_and_launches(scenario):
    f = fixture(scenario=scenario)
    result = f.run()
    assert not result['accepted']
    assert f.board.tracks == f.baseline and f.board.physics == f.base_physics
    assert f.board.builds >= 1
    if scenario == 'expired':
        assert len(f.calls) == 1


@pytest.mark.parametrize('scenario', ['empty-port', 'partial-alias', 'same-geometry', 'mutate-retained', 'orphan-owned'])
def test_source_contract_violations_abort_before_next_trial(scenario):
    f = fixture(scenario=scenario)
    with pytest.raises(ValueError):
        f.run()
    assert len(f.calls) == 1
    assert f.board.tracks == f.baseline and f.board.physics == f.base_physics


def test_unexpected_exception_cleans_added_and_retained_trial_copper():
    f = fixture(scenario='fatal')
    with pytest.raises(RuntimeError, match='unexpected native fault'):
        f.run()
    assert f.board.tracks == f.baseline and f.board.physics == f.base_physics
    assert len(f.calls) == 1 and f.board.builds == 1


def test_targets_follow_physical_order_and_never_exceed_four():
    f = fixture(scenario='all-empty', count=6)
    result = f.run([tuple(q['pad_ids']) for q in reversed(f.ports)])
    assert not result['accepted']
    assert [q[0] for q in f.calls] == ['signal-' + str(i) for i in range(4)]
    assert f.board.tracks == f.baseline


def test_partial_alias_feedback_never_authorizes_a_target():
    f = fixture()
    with pytest.raises(ValueError):
        f.run([('pad-signal-0',)])
    assert not f.calls and f.board.tracks == f.baseline


def test_expired_source_end_does_not_admit_any_trial():
    f = fixture()
    f.clock.now = 10.
    assert not f.run()['accepted']
    assert not f.calls and f.board.tracks == f.baseline


def test_advancing_clock_cannot_give_each_target_a_new_deadline():
    f = fixture(scenario='all-empty', count=6, clock_step=2.)
    assert not f.run()['accepted']
    assert 0 < len(f.calls) < 4
    assert all(q[1]['deadline'] == 10. for q in f.calls)
    assert f.board.tracks == f.baseline and f.board.physics == f.base_physics
