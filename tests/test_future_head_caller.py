"""Execute conditional attempt control flow with explicit nonphysical boundaries.

Native creation/graph/physics are separate qualification gates. These controls
ensure the caller presents the correct physical/launch frame to those gates.
"""
import ast
import builtins
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from smilepcb.keyboard_routing.conditional_sources import SourceSearchBudget

ROOT = Path(__file__).resolve().parents[1]
BANKS = ROOT / 'smilepcb/keyboard_routing/conditional_banks.py'


def launch_module():
    path = ROOT / 'smilepcb/keyboard_routing/interface_launches.py'
    spec = importlib.util.spec_from_file_location('future_caller_launches', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def port(net, uid, pad=None):
    return {'net': net, 'pad_ids': [pad or 'pad-' + net], 'added_ids': [uid],
            'terminal_id': uid, 'exit_nm': [10, 0], 'exit_layer': 0}


def copper(net, start=0):
    return {'net': net, 'class': 'PCB_TRACK', 'layer': 0, 'width': 150000,
            'start': [start, 0], 'end': [start + 10, 0], 'locked': False}


class Board:
    def __init__(self, tracks):
        self.tracks = deepcopy(tracks)

    def GetDesignSettings(self):
        return SimpleNamespace(m_MinClearance=173000, m_TrackMinWidth=150000)

    def GetFootprints(self):
        return [SimpleNamespace(GetReference=lambda: 'U_MCU', GetLayer=lambda: 0)]

    def GetTracks(self):
        return [SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda u=u: u)) for u in self.tracks]

    def Delete(self, item):
        del self.tracks[item.m_Uuid.AsString()]

    def BuildConnectivity(self):
        pass


def attempt_fixture(*, fail=None, with_prefix=True, final_missing=False, with_future=True, mixed_source=False):
    launches = launch_module()
    original = port('current', 'current-head')
    future = port('future', 'future-head')
    old_prefix = port('prior', 'old-prior-head')
    live_prefix = port('prior', 'prior-head')
    fixed = port('fixed', 'fixed-head')
    heads = {'current-head': copper('current'), 'future-head': copper('future', 30),
             'old-prior-head': copper('prior', 60)}
    banks = {'current-bank': [original]}
    if with_future:
        banks['future-bank'] = [future]
    prefix = []
    base = {'fixed-head': copper('fixed', 90), 'field': copper('field', 100)}
    if with_prefix:
        banks['prior-bank'] = [old_prefix]
        base['prior-head'] = copper('prior', 61)
        prefix = [{'bank': 'prior-bank', 'id': 'previous:regenerated',
                   'proof': {'geometry_sha256': 'prior-proof'}}]
    board = Board(base)
    events = []
    journals = []
    proofs = []
    snapshots = []
    result = {'attempts': []}
    clock = SimpleNamespace(monotonic=lambda: 0.)

    def snapshot(actual):
        assert actual is board
        return {'tracks': deepcopy(board.tracks), 'footprints': {}}

    parent = snapshot(board)

    def scope_nets():
        return {q['net'] for q in launches.current_launches()}

    prior_nets = {'prior'} if with_prefix else set()
    base_scope = {'fixed'} | prior_nets

    def expect_future(stage, *, current=False):
        events.append(stage)
        expected = heads['future-head'] if with_future else None
        assert board.tracks.get('future-head') == expected, (
            'Future original head absent or unexpectedly installed during ' + stage)
        assert scope_nets() == base_scope | ({'future'} if with_future else set()) | ({'current'} if current else set()), (
            'Future launch reservation absent or scope polluted during ' + stage)
        assert 'old-prior-head' not in board.tracks

    def emit(actual, shapes):
        assert actual is board
        assert not shapes.keys() & board.tracks.keys()
        board.tracks.update(deepcopy(shapes))

    def admit(router, shapes, ports, *, deadline):
        if mixed_source and deadline == 10.:
            # This single-interface bank has no other originals to retain.
            assert shapes == {} and ports == []
            expect_future('mixed-retained-admission')
            return {'accepted': True, 'failed_interfaces': []}
        assert deadline == 25.
        nets = {q['net'] for q in ports}
        if nets == {'future'}:
            assert not shapes.keys() & board.tracks.keys()
            assert scope_nets() == base_scope
            events.append('future-admission')
            if fail == 'future':
                return {'accepted': False, 'failed_interfaces': [['pad-future']]}
        else:
            assert nets == {'current'}
            expect_future('original-source')
            if fail == 'source':
                return {'accepted': False, 'failed_interfaces': [['pad-current']]}
        return {'accepted': True, 'failed_interfaces': []}

    generated_calls = []
    def generate(actual, **kwargs):
        generated_calls.append(kwargs)
        assert actual is board and kwargs['include_nets'] == {'current'}
        expect_future('regenerated-source')
        assert 'current-head' not in board.tracks
        if mixed_source and len(generated_calls) == 1:
            return {'accepted': False, 'reason': 'No legal outward interface candidates'}
        if fail == 'source':
            return {'accepted': False, 'reason': 'source failed'}
        emit(board, {'new-current-head': copper('current', 5)})
        return {'accepted': True, 'ports': [port('current', 'new-current-head')]}

    def collectors(actual, cfg, ports, **kwargs):
        assert actual is board and {q['net'] for q in ports} == {'current'}
        expect_future('collectors', current=True)
        if fail == 'fatal-collectors':
            raise RuntimeError('unexpected collector error')
        if fail == 'collectors':
            raise ValueError('collector failure')
        emit(board, {'collector': copper('current', 11)})
        advanced = deepcopy(ports[0])
        advanced['added_ids'].append('collector')
        advanced['terminal_id'] = 'collector'
        advanced['exit_nm'] = [21, 0]
        launches.publish_launches([advanced])
        return {'accepted': True}

    def begin(actual, selected):
        assert actual is board and selected == {'current'}
        expect_future('journal', current=True)
        journal = SimpleNamespace(frame=snapshot(board), bank_nets=set(selected), owners={})
        journals.append(journal)
        return journal

    class Router:
        def __init__(self, actual, *, deadline, **kwargs):
            assert actual is board
            self.deadline = deadline
            self.graph_diagnostic = {}

        def refresh_interfaces(self, graph):
            pass

        def expired(self):
            return False

    def route(actual, router, nets, **kwargs):
        assert actual is board and nets == {'current'}
        expect_future('feeders', current=True)
        if fail == 'feeders':
            raise ValueError('feeder failure')
        emit(board, {'feeder': copper('current', 22)})
        journals[-1].owners['feeder'] = deepcopy(board.tracks['feeder'])
        router.graph_diagnostic = {'attempts': []}
        return 1

    def graph(actual, nets, deadline):
        assert actual is board
        value = {'nets': set(nets), 'tracks': deepcopy(board.tracks)}
        ids = set(board.tracks) | {u for q in [original, future, live_prefix, fixed] for u in q['pad_ids']}
        value.update(nodes=[{'item': SimpleNamespace(m_Uuid=SimpleNamespace(AsString=lambda u=u: u))}
                            for u in sorted(ids)], find=lambda i: 0)
        snapshots.append(value)
        return value

    def state(graph, selected):
        missing = {'current': 1} if 'current' in selected and 'feeder' not in graph['tracks'] else {}
        if final_missing and 'future-head' not in graph['tracks'] and 'current' in selected:
            missing = {'current': 1}
        return {'missing': missing, 'groups': {net: 1 for net in selected}}

    def proof(actual, added, g, covered, deadline):
        assert actual is board and g['tracks'] == board.tracks
        assert added and all(q['net'] == 'current' for q in added.values())
        assert not set(added) & {'future-head', 'fixed-head', 'prior-head', 'field'}
        proofs.append({'tracks': deepcopy(board.tracks), 'scope': scope_nets(),
                       'added': deepcopy(added), 'graph': g})
        return len(added)

    class TemporaryHeads:
        """Stateful physical boundary double; caller controls lifecycle and scope."""
        def __init__(self, actual, shapes, ports, physical_sha256):
            assert actual is board
            assert shapes == ({'future-head': heads['future-head']} if with_future else {})
            assert ports == ([future] if with_future else [])
            self.shapes = deepcopy(shapes)
            self.ports = deepcopy(ports)
            self.installed = False

        def install(self, *, deadline):
            emit(board, self.shapes)
            self.installed = bool(self.shapes)
            self.frame = snapshot(board)
            return self

        def verify_ports(self):
            assert [q for q in launches.current_launches() if q['net'] == 'future'] == ([future] if with_future else [])

        def retire(self, *, deadline=None):
            if not self.installed:
                return
            assert all(board.tracks.get(uid) == shape for uid, shape in self.shapes.items())
            for uid in self.shapes:
                del board.tracks[uid]
            self.installed = False
            events.append('retired')

        def __enter__(self):
            return self.install(deadline=25.)

        def __exit__(self, *error):
            if self.installed:
                self.retire(deadline=25.)

    def future_heads(actual_banks, bank, actual_prefix, actual_heads):
        assert actual_banks == banks and bank == 'current-bank' and actual_prefix == prefix
        assert actual_heads == heads
        return (deepcopy([future]), {'future-head': deepcopy(heads['future-head'])}) if with_future else ([], {})

    def parent_signature(frame, ports):
        assert frame == parent
        assert ports == ([live_prefix] if with_prefix else [])
        return 'headless-parent-signature'

    def rebuild(actual_prefix):
        assert actual_prefix == prefix
        return board, deepcopy(parent), deepcopy([live_prefix] if with_prefix else [])

    def retained(actual, frame):
        assert actual is board
        return all(board.tracks.get(uid) == shape for uid, shape in frame['tracks'].items())

    modules = {
        'controller_ports': SimpleNamespace(route_controller_ports=generate),
        'collectors': SimpleNamespace(route_collectors=collectors),
        'owned_feeder_repair': SimpleNamespace(begin_feeders=begin),
        'safety': SimpleNamespace(require_via_land_clearance=lambda *args, **kwargs: None),
    }
    original_import = builtins.__import__

    def importer(name, globals=None, locals=None, fromlist=(), level=0):
        return modules[name] if level == 1 and name in modules else original_import(name, globals, locals, fromlist, level)

    env = {'__builtins__': {**vars(builtins), '__import__': importer},
           '__package__': 'smilepcb.keyboard_routing', 'deepcopy': deepcopy, 'time': clock,
           'banks': banks, 'heads': heads, 'normal_router': Router, 'normal_route': route,
           'result': result, 'rebuild': rebuild, 'fixed': [fixed], 'snapshot': snapshot,
           'parent_signature': parent_signature, 'reserve_launches': launches.reserve_launches,
           'current_launches': launches.current_launches, 'copper_graph': graph, '_pad_state': state,
           'expected_pads': lambda g, nets: None, 'LocalRouter': Router, '_admit_heads': admit,
           'emit_shapes': emit, 'interface_identity': launches.interface_identity,
           'geometry_signature': lambda value: repr(sorted(value.items())), '_retained': retained,
           'cfg': {'parts': [{'ref': 'U_MCU', 'region': 'controller'}], 'plane_nets': set()},
           'exit_bounds_nm': None, 'width': 150000, 'field_ids': {'field'},
           'cutoffs': {'enabled': False},
           'repair_ledger': SimpleNamespace(remaining=lambda bank: 120., admit=lambda *args: None),
           '_journaled_feeder_callers': lambda actual, journal, normal: (Router, route),
           '_prove_copper': proof, 'base_sha': 'base-signature',
           'current_generation_profile': lambda: SimpleNamespace(name='host-profile'),
           '_future_heads': future_heads, '_TemporaryHeads': TemporaryHeads,
           '_source_physics': lambda *args: 'source-physics', 'board': board,
           'SourceSearchBudget': SourceSearchBudget,
           'FrozenProofRouter': SimpleNamespace(_physical_state=lambda actual, excluded_ids=():
               {u:q for u,q in actual.board.tracks.items() if u not in excluded_ids}),
           'SimpleNamespace': SimpleNamespace}
    tree = ast.parse(BANKS.read_text())
    attempt = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'attempt')
    mixed = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_mixed_source_heads')
    factory = ast.FunctionDef(name='_attempt_factory', args=ast.arguments(posonlyargs=[], args=[],
        kwonlyargs=[], kw_defaults=[], defaults=[]), body=[
            ast.Assign(targets=[ast.Name(id='best_partial', ctx=ast.Store())], value=ast.Constant(value=None)),
            attempt, ast.Return(value=ast.Name(id='attempt', ctx=ast.Load()))], decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[mixed, factory], type_ignores=[]))
    exec(compile(module, str(BANKS), 'exec'), env)
    env['attempt'] = env['_attempt_factory']()
    outer = port('outside', 'outside-head')

    def run(option):
        with launches.reserve_launches([outer]):
            answer = env['attempt']('current-bank', option, prefix,
                                    [tuple(original['pad_ids'])] if mixed_source else [], 100.)
            assert tuple(launches.current_launches()) == (outer,), 'Attempt leaked a launch scope'
        assert launches.current_launches() == ()
        return answer

    return SimpleNamespace(run=run, board=board, parent=parent, events=events,
                           journals=journals, proofs=proofs, snapshots=snapshots,
                           launches=launches, result=result, generated_calls=generated_calls)


@pytest.mark.parametrize('option', ['original', 'regenerated'])
@pytest.mark.parametrize('with_prefix', [False, True])
def test_future_sources_guard_all_routing_but_never_enter_current_plan(option, with_prefix):
    fixture = attempt_fixture(with_prefix=with_prefix)
    result = fixture.run(option)
    assert result['accepted'], result.get('reason')
    plan = result['plan']
    head = 'current-head' if option == 'original' else 'new-current-head'
    assert set(plan['shapes']) == {head, 'collector', 'feeder'}
    assert plan['head_ids'] == [head]
    assert plan['parent_sha256'] == 'headless-parent-signature'
    assert plan['ports'] == [{'net': 'current', 'pad_ids': ['pad-current'],
                              'added_ids': [head, 'collector'], 'terminal_id': 'collector',
                              'exit_nm': [21, 0], 'exit_layer': 0}]
    assert 'future-head' not in fixture.board.tracks
    assert all(fixture.board.tracks[uid] == value for uid, value in fixture.parent['tracks'].items())
    assert set(fixture.journals[0].owners) == {'feeder'}
    assert 'future-head' in fixture.journals[0].frame['tracks']
    assert fixture.events.index('collectors') < fixture.events.index('journal') < fixture.events.index('feeders') < fixture.events.index('retired')
    assert any('future-head' in proof['tracks'] for proof in fixture.proofs)
    assert 'future-head' not in fixture.proofs[-1]['tracks']
    assert fixture.proofs[-1]['scope'] == {'current', 'fixed'} | ({'prior'} if with_prefix else set())


@pytest.mark.parametrize('stage', ['source', 'collectors', 'feeders'])
@pytest.mark.parametrize('option', ['original', 'regenerated'])
def test_failed_attempt_restores_outer_scope_and_never_returns_a_plan(stage, option):
    fixture = attempt_fixture(fail=stage)
    result = fixture.run(option)
    assert not result['accepted'] and result['plan'] is None
    assert result['prefix_preserved']
    assert 'future-head' not in fixture.board.tracks
    assert fixture.launches.current_launches() == ()


@pytest.mark.parametrize('option', ['original', 'regenerated'])
def test_fresh_graph_after_temporary_retirement_remains_authoritative(option):
    fixture = attempt_fixture(final_missing=True)
    result = fixture.run(option)
    assert not result['accepted'] and result['plan'] is None
    assert result['missing'] == {'current': 1}
    assert result['failed_interfaces'] == [['pad-current']]
    assert not fixture.proofs or 'future-head' in fixture.proofs[-1]['tracks']


def test_bad_prefix_cannot_install_future_heads_after_failed_admission():
    fixture = attempt_fixture(fail='future')
    result = fixture.run('original')
    assert not result['accepted'] and result['plan'] is None
    assert result['failed_interfaces'] == []  # No current-source regeneration authority.
    assert fixture.events == ['future-admission']
    assert fixture.board.tracks == fixture.parent['tracks']
    assert fixture.launches.current_launches() == ()


def test_unexpected_routing_exception_still_cleans_temporary_heads_and_scope():
    fixture = attempt_fixture(fail='fatal-collectors')
    with pytest.raises(RuntimeError, match='unexpected collector error'):
        fixture.run('regenerated')
    assert 'future-head' not in fixture.board.tracks
    assert fixture.launches.current_launches() == ()
    assert fixture.result['attempts'][-1]['accepted'] is False


@pytest.mark.parametrize('option', ['original', 'regenerated'])
def test_last_bank_has_no_temporary_admission_or_proof_but_keeps_final_gates(option):
    fixture = attempt_fixture(with_future=False)
    result = fixture.run(option)
    assert result['accepted'], result.get('reason')
    head = 'current-head' if option == 'original' else 'new-current-head'
    assert result['plan']['head_ids'] == [head]
    assert set(result['plan']['shapes']) == {head, 'collector', 'feeder'}
    assert result['plan']['ports'][0]['terminal_id'] == 'collector'
    assert result['creation_checked_items'] == 3 and result['missing'] == {}
    assert result['prefix_preserved']
    assert 'future_head_admission' not in result
    assert 'future_reservation_checked_items' not in result
    assert 'future-admission' not in fixture.events and 'retired' not in fixture.events
    assert 'future-head' not in fixture.journals[0].frame['tracks']
    assert len(fixture.proofs) == 1
    assert fixture.proofs[0]['scope'] == {'current', 'prior', 'fixed'}
    assert fixture.launches.current_launches() == ()


@pytest.mark.parametrize('incomplete', [False, True])
def test_mixed_source_returns_to_all_original_bank_and_future_proof_gates(incomplete):
    fixture = attempt_fixture(mixed_source=True, final_missing=incomplete)
    result = fixture.run('regenerated')
    assert result['mixed_head_generation']['accepted']
    assert result['source_strategy'] == 'mixed_single_interface'
    assert len(fixture.generated_calls) == 2
    assert fixture.generated_calls[0]['source_budget'] is fixture.generated_calls[1]['source_budget']
    assert result['accepted'] is (not incomplete)
    assert result['plan'] is None if incomplete else result['plan']['head_ids'] == ['new-current-head']
    assert result['prefix_preserved'] and 'future-head' not in fixture.board.tracks
    assert 'collectors' in fixture.events and 'feeders' in fixture.events
    assert all(q['net'] == 'current' for proof in fixture.proofs for q in proof['added'].values())
    assert fixture.launches.current_launches() == ()


def test_mixed_source_success_does_not_hide_unexpected_later_routing_exception():
    fixture = attempt_fixture(mixed_source=True, fail='fatal-collectors')
    with pytest.raises(RuntimeError, match='unexpected collector error'):
        fixture.run('regenerated')
    assert fixture.result['attempts'][-1]['mixed_head_generation']['accepted']
    assert not fixture.result['attempts'][-1]['accepted']
    assert 'future-head' not in fixture.board.tracks
    assert fixture.launches.current_launches() == ()
