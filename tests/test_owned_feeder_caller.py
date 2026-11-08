"""Caller control-flow contracts with explicit nonphysical native boundaries.

The separate automatic native fixture proves routing/geometry; these controls
exercise actual caller bodies and reject deadline/ownership/proof bypasses.
"""
import ast
import builtins
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import math

import pytest

ROOT = Path(__file__).resolve().parents[1]
BANKS = ROOT / 'smilepcb/keyboard_routing/conditional_banks.py'
MATRIX = ROOT / 'smilepcb/keyboard_routing/matrix_stage.py'


def compile_nodes(path, nodes, ns):
    env = {'deepcopy': deepcopy, 'math': math, '__name__': 'caller_host',
           '__package__': 'smilepcb.keyboard_routing', **ns}
    tree = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(tree, str(path), 'exec'), env)
    return env


def definition(name, ns=None):
    nodes = [n for n in ast.parse(BANKS.read_text()).body
             if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name == name]
    assert len(nodes) == 1, 'Expected caller seam ' + name
    return compile_nodes(BANKS, nodes, ns or {})[name]


def function(body, args=()):
    return ast.FunctionDef(name='run', args=ast.arguments(posonlyargs=[],
        args=[ast.arg(arg=n) for n in args], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=body, decorator_list=[])


def import_boundary(api):
    original = builtins.__import__
    def importer(name, globals=None, locals=None, fromlist=(), level=0):
        if name in ('owned_feeder_repair', 'smilepcb.keyboard_routing.owned_feeder_repair'):
            return api
        return original(name, globals, locals, fromlist, level)
    return {**vars(builtins), '__import__': importer}


def test_ledger_splits_only_explicit_extra_and_never_replenishes_bank():
    ledger = definition('_BankRepairLedger')({'first': [], 'second': []}, 100.)
    assert ledger.remaining('first') == 50. and ledger.remaining('second') == 50.
    # Same object is reused for a later option and a different prefix retry.
    first = ledger.charge('first', 10., 17.)
    second = ledger.charge('first', 30., 34.)
    assert first == {'elapsed_s': 7., 'spent_s': 7., 'remaining_s': 43.}
    assert second == {'elapsed_s': 4., 'spent_s': 11., 'remaining_s': 39.}
    assert ledger.remaining('second') == 50.


def test_ledger_cap_and_shared_proof_tail_do_not_extend_the_caller():
    ledger = definition('_BankRepairLedger')({'a': [], 'b': []}, 1000.)
    assert ledger.remaining('a') == 120.
    assert ledger.admit('a', 10., 25., 90.) == 82.
    ledger.charge('a', 10., 140.)
    assert ledger.remaining('a') == 0.
    assert ledger.admit('a', 141., 150., 500.) is None


@pytest.mark.parametrize('extra,now,ordinary,shared', [
    (0., 0., 25., 100.), (5., 0., 25., 100.),
    (100., 80., 90., 90.), (100., 80., 81., 90.)])
def test_no_recovery_admission_without_extra_tail_and_setup_margin(extra, now, ordinary, shared):
    ledger = definition('_BankRepairLedger')({'bank': []}, extra)
    assert ledger.admit('bank', now, ordinary, shared) is None


def test_ledger_charges_whole_actual_phase_including_setup_proof_and_cleanup():
    ledger = definition('_BankRepairLedger')({'bank': []}, 40.)
    # Actual phase starts before setup, not when routing starts at t=7.
    end = ledger.admit('bank', 5., 25., 100.)
    assert end == 45.
    charged = ledger.charge('bank', 5., 35.)
    assert charged['elapsed_s'] == 30. and ledger.remaining('bank') == 10.
    assert ledger.admit('bank', 36., 47., 100.) is None


def matrix_forward(deadline, recovery):
    tree = ast.parse(MATRIX.read_text())
    outer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_prepare_matrix')
    branch = next(n for n in outer.body if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                  and isinstance(n.test.left, ast.Name) and n.test.left.id == 'generated_heads')
    calls = []
    def conditional(*args, **kwargs):
        calls.append(kwargs)
        return {'supported': False, 'accepted': False}
    def importer(name, globals=None, locals=None, fromlist=(), level=0):
        assert name == 'conditional_banks' and level == 1
        return SimpleNamespace(prepare_conditional_banks=conditional)
    ns = {'__builtins__': {**vars(builtins), '__import__': importer},
          'deadline': deadline, 'recovery_deadline': recovery, 'generated_heads': object(),
          'board': object(), 'cfg': {}, 'report': {}, 'ports': [], 'exit_bounds_nm': None}
    run = compile_nodes(MATRIX, [function([branch], ('deadline', 'recovery_deadline'))], ns)['run']
    run(deadline, recovery)
    assert len(calls) == 1
    return calls[0]


@pytest.mark.parametrize('deadline,recovery,extra,stop', [
    (100., None, 0., 88.), (100., 100., 0., 88.),
    (100., 90., 0., 88.), (100., 160., 60., 148.)])
def test_matrix_forwards_only_explicit_absolute_extra(deadline, recovery, extra, stop):
    actual = matrix_forward(deadline, recovery)
    assert actual.get('repair_extra_seconds') == extra
    assert actual['deadline'] == stop


def journal_wrappers(*, expire=False, route_error=False):
    board = SimpleNamespace(tracks={'prefix': {'net': 'fixed'}})
    journal = SimpleNamespace(mark='original')
    events = []
    class Router:
        def __init__(self):
            self.added = ['previous-indexed-object']
            self.deadline = 10.
            self.graph_diagnostic = {}
        def begin_route(self, net): events.append(('begin', net))
        def expired(self): return expire
    observed = Router()
    def constructor(actual, j, *, deadline):
        assert actual is board and j is journal
        observed.deadline = deadline
        events.append(('constructor', deadline))
        return observed
    def snap(actual):
        assert actual is board
        return {'tracks': deepcopy(board.tracks), 'footprints': {}}
    def record(actual, j, before, ids, net):
        assert actual is board and j is journal
        assert before == {'tracks': {'prefix': {'net': 'fixed'}}, 'footprints': {}}
        assert ids == ['new-feeder'] and net == 'signal'
        assert board.tracks['new-feeder'] == {'net': 'signal'}
        events.append(('record', net))
    def native(actual, router, nets, *, finer_grid_steps):
        assert actual is board and router is observed and nets == {'signal'}
        assert finer_grid_steps == (125000,)
        events.append(('native', 'signal'))
        board.tracks['new-feeder'] = {'net': 'signal'}
        router.added.append('new-feeder')
        if route_error: raise RuntimeError('actual ordinary route failure')
        return 7
    api = SimpleNamespace(feeder_router=constructor, record_feeder_route=record)
    factory = definition('_journaled_feeder_callers', {
        'snapshot': snap, '__builtins__': import_boundary(api),
        'feeder_router': constructor, 'record_feeder_route': record})
    construct, connect = factory(board, journal, native)
    world = construct(board, deadline=10., compact=True, track_width_nm=150000)
    assert world is observed
    return board, events, lambda: connect(board, world, {'signal'}, finer_grid_steps=(125000,))


def test_observed_call_records_only_exact_normal_indexed_suffix():
    board, events, call = journal_wrappers()
    assert call() == 7
    assert events == [('constructor', 10.), ('begin', 'signal'), ('native', 'signal'), ('record', 'signal')]


def test_observer_setup_expiry_never_calls_or_journals_native_route():
    board, events, call = journal_wrappers(expire=True)
    assert call() == 0
    assert events == [('constructor', 10.), ('begin', 'signal')]
    assert board.tracks == {'prefix': {'net': 'fixed'}}


def test_ordinary_route_exception_propagates_without_granting_journal_authority():
    board, events, call = journal_wrappers(route_error=True)
    with pytest.raises(RuntimeError, match='actual ordinary route failure'):
        call()
    assert events == [('constructor', 10.), ('begin', 'signal'), ('native', 'signal')]
    assert 'new-feeder' in board.tracks  # Caller owns aborting this scratch bank.


def test_final_fresh_native_missing_rejects_even_after_repair_claims_improvement():
    tree = ast.parse(BANKS.read_text())
    seq = next(n.body for n in ast.walk(tree) if isinstance(n, ast.With)
               and any(isinstance(c, ast.Assign) and isinstance(c.value, ast.Constant)
                       and c.value.value == 'complete_bank_proof' for c in n.body))
    start = next(i for i, n in enumerate(seq) if isinstance(n, ast.Assign)
                 and isinstance(n.value, ast.Constant) and n.value.value == 'complete_bank_proof')
    stop = next(i for i in range(start, len(seq)) if isinstance(seq[i], ast.If)
                and isinstance(seq[i].test, ast.Subscript) and isinstance(seq[i].test.value, ast.Name)
                and seq[i].test.value.id == 'state' and isinstance(seq[i].test.slice, ast.Constant)
                and seq[i].test.slice.value == 'missing')
    seen = []
    row = {'owned_feeder_repair': {'recovery': {'improved': True, 'missing': {}}}}
    def graph(*args):
        seen.append('fresh graph')
        return {'missing': {'signal': 1}}
    run = compile_nodes(BANKS, [function(seq[start:stop+1])], {
        'row': row, 'repair_started': 0., 'check': object(), 'covered': {'signal'}, 'stop': 100.,
        'selected': {'signal'}, 'prefix_nets': set(), 'qs': [{'net': 'signal', 'pad_ids': ['alias-a','alias-b']}],
        'copper_graph': graph, '_pad_state': lambda g, nets: g,
        'expected_pads': lambda g, nets: seen.append(('expected pads', nets)),
        'cutoffs': {'enabled': False}})['run']
    with pytest.raises(ValueError, match='complete_bank_routing_incomplete'):
        run()
    assert seen == ['fresh graph', ('expected pads', {'signal'})]
    assert row['failed_interfaces'] == [['alias-a','alias-b']]
    assert row.get('accepted') is not True


@pytest.mark.parametrize('extra', [0., 40.])
def test_zero_pool_bypasses_journal_and_observer_binding(extra):
    tree = ast.parse(BANKS.read_text())
    blocks = [n for n in ast.walk(tree) if isinstance(n, ast.If)
              and isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Call)
              and isinstance(n.test.left.func, ast.Attribute) and n.test.left.func.attr == 'remaining'
              and isinstance(n.test.left.func.value, ast.Name) and n.test.left.func.value.id == 'repair_ledger']
    assert len(blocks) <= 1
    calls = []
    board = object(); journal = object()
    normal_constructor, normal_connect, observed_constructor, observed_connect = (object() for _ in range(4))
    def begin(actual, nets):
        assert actual is board and nets == {'signal'}
        calls.append('journal')
        return journal
    def wrap(actual, current, connect):
        assert actual is board and current is journal and connect is normal_connect
        calls.append('observer')
        return observed_constructor, observed_connect
    body = list(blocks) + [ast.Return(ast.Tuple([ast.Name('KeyboardRouter',ast.Load()),
                                               ast.Name('route_components',ast.Load())],ast.Load()))]
    run = compile_nodes(BANKS, [function(body, ('KeyboardRouter','route_components'))], {
        '__builtins__': import_boundary(SimpleNamespace(begin_feeders=begin)),
        'repair_ledger': SimpleNamespace(remaining=lambda bank: extra), 'bank': 'physical-bank',
        'check': board, 'selected': {'signal'}, 'normal_route': normal_connect,
        '_journaled_feeder_callers': wrap})['run']
    answer = run(normal_constructor, normal_connect)
    assert answer == ((observed_constructor,observed_connect) if extra else (normal_constructor,normal_connect))
    assert calls == (['journal','observer'] if extra else [])


def execute_attempt_finally(*, phase_started=2., retained_cost=2., stop=30., retained_error=False, temporary=None):
    tree = ast.parse(BANKS.read_text())
    attempt = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'attempt')
    final = next(n.finalbody for n in attempt.body if isinstance(n, ast.Try) and n.finalbody)
    clock = SimpleNamespace(now=12.)
    clock.monotonic = lambda: clock.now
    ledger = definition('_BankRepairLedger')({'bank': []}, 40.)
    row = {'accepted': True, 'failed_interfaces': [['old-certificate']], 'owned_feeder_repair': {}}
    result = {'attempts': []}
    def retained(board, parent):
        clock.now += retained_cost
        if retained_error: raise RuntimeError('retained-prefix observation failed')
        return True
    body = deepcopy(final) + [ast.Return(ast.Name('plan',ast.Load()))]
    run = compile_nodes(BANKS, [function(body, ('plan',))], {
        'time': clock, 'row': row, 'result': result, 'tick': 0., 'temporary': temporary,
        'check': object(), 'parent': object(), '_retained': retained,
        'repair_started': phase_started, 'repair_ledger': ledger, 'bank': 'bank', 'stop': stop})['run']
    return run, clock, ledger, row, result


def test_recovery_finally_charges_retained_observation_and_keeps_valid_plan():
    run, clock, ledger, row, result = execute_attempt_finally()
    plan = object()
    assert run(plan) is plan
    assert row['accepted'] and ledger.remaining('bank') == 28.
    assert row['owned_feeder_repair']['elapsed_s'] == 12.
    assert result['attempts'] == [row]


def test_recovery_finally_charges_even_when_retained_observation_raises():
    run, clock, ledger, row, result = execute_attempt_finally(retained_error=True)
    with pytest.raises(RuntimeError, match='retained-prefix observation failed'):
        run(object())
    assert ledger.remaining('bank') == 28. and result['attempts'] == [row]


def test_recovery_cannot_accept_after_final_retained_check_uses_its_deadline():
    run, clock, ledger, row, result = execute_attempt_finally(stop=13.)
    assert run(object()) is None
    assert row['accepted'] is False and row['failed_interfaces'] == []
    assert ledger.remaining('bank') == 28. and result['attempts'] == [row]


def test_default_path_does_not_charge_or_apply_a_new_recovery_admission_gate():
    run, clock, ledger, row, result = execute_attempt_finally(phase_started=None, stop=13.)
    plan = object()
    assert run(plan) is plan and row['accepted'] is True
    assert ledger.remaining('bank') == 40. and row['owned_feeder_repair'] == {}


def recovery_helper(*, route_error=None, dishonest_improvement=False, graph_timeout=False):
    order = ['beta', 'alpha']
    missing = {net: 1 for net in order}
    journal = SimpleNamespace(bank_nets=set(order), owners={},
                              witnesses={net: {'stale': {'net': 'retired'}} for net in order})
    board = SimpleNamespace(GetDesignSettings=lambda: SimpleNamespace(m_TrackMinWidth=150000))
    clock = SimpleNamespace(now=0.)
    clock.monotonic = lambda: clock.now
    worlds, routed, repairs, graphs = [], [], [], []
    native_route = object()
    def callers(actual, j, route):
        assert actual is board and j is journal and route is native_route
        def constructor(current, *, deadline, compact, track_width_nm):
            assert current is board and compact and track_width_nm == 150000
            router = SimpleNamespace(deadline=deadline, graph_diagnostic={'attempts': []}, query_omissions=0)
            router.expired = lambda: clock.now >= router.deadline
            worlds.append(router)
            return router
        def connect(current, router, nets, *, finer_grid_steps):
            target = next(iter(nets))
            assert current is board and not finer_grid_steps
            assert journal.witnesses[target] == {}  # Cleared by real helper.
            assert router.grid_step == 125000 and router.search_budget_s == router.connection_budget_s == 4
            assert router.routing_penalty is None and router.deadline <= 5. + clock.now
            routed.append(target)
            if route_error is not None:raise route_error('ordinary routing failed')
            journal.witnesses[target] = {'new': {'net': 'observed'}}
            clock.now += 1.
            return 0
        return constructor, connect
    def graph(current, nets, deadline):
        assert current is board and nets == set(order) and deadline == 30.
        graphs.append(tuple(sorted(nets)))
        if graph_timeout:
            clock.now = deadline
            raise TimeoutError('read-only native assessment expired')
        return {'missing': dict(missing)}
    def repair(current, j, target, *, deadline):
        assert current is board and j is journal and deadline == 30.
        assert j.witnesses[target] == {'new': {'net': 'observed'}}
        repairs.append(target)
        return SimpleNamespace(improved=dishonest_improvement, reason='finite',
            before_missing=dict(missing), after_missing={} if dishonest_improvement else dict(missing),
            retired_ids=(), added_shapes={}, attempts=())
    run = definition('_recover_owned_feeders', {
        '__builtins__': import_boundary(SimpleNamespace(repair_owned_feeders=repair)),
        'time': clock, 'route_components': native_route, '_journaled_feeder_callers': callers,
        'geometry_signature': lambda owners: repr(sorted(owners.items())),
        'copper_graph': graph, '_pad_state': lambda g, nets: g})
    return lambda: run(board,journal,[{'net':net} for net in order],{'missing':dict(missing)},deadline=30.), worlds,routed,repairs,graphs


def test_recovery_uses_fresh_observed_worlds_and_stops_unchanged_native_state():
    run, worlds, routed, repairs, graphs = recovery_helper()
    result = run()
    assert routed == repairs == ['beta', 'alpha']
    assert len(worlds) == 2 and worlds[0] is not worlds[1]
    assert len(graphs) == 4 and result['missing'] == {'beta': 1, 'alpha': 1}
    assert result['reason'] == 'no_strict_native_progress' and len(result['history']) == 2


@pytest.mark.parametrize('error', [RuntimeError, TimeoutError])
def test_recovery_does_not_turn_actual_route_exceptions_into_readonly_timeout(error):
    run, worlds, routed, repairs, graphs = recovery_helper(route_error=error)
    with pytest.raises(error, match='ordinary routing failed'):
        run()
    assert routed == ['beta'] and not repairs and not graphs


def test_recovery_rejects_api_success_hint_that_fresh_native_graph_disagrees_with():
    run, worlds, routed, repairs, graphs = recovery_helper(dishonest_improvement=True)
    with pytest.raises(ValueError, match='differs from the current native bank'):
        run()
    assert routed == repairs == ['beta'] and len(graphs) == 2


def test_readonly_assessment_expiry_reports_old_state_without_claiming_closure():
    run, worlds, routed, repairs, graphs = recovery_helper(graph_timeout=True)
    result = run()
    assert result['reason'] == 'repair_budget_exhausted'
    assert result['missing'] == {'beta': 1, 'alpha': 1}
    assert result['missing_basis'] == 'last_completed_native_graph'
    assert routed == ['beta'] and not repairs and len(graphs) == 1


def test_temporary_cleanup_is_charged_before_recovery_deadline_acceptance():
    temporary = SimpleNamespace(installed=True)
    run, clock, ledger, row, result = execute_attempt_finally(temporary=temporary, stop=18.)
    def retire():
        clock.now += 5.
        temporary.installed = False
    temporary.retire = retire
    assert run(object()) is None
    assert row['accepted'] is False and row['reason'] == 'owned_feeder_repair_deadline'
    assert ledger.remaining('bank') == 23.  # 12 + 5 cleanup + 2 retained - 2 start.


def test_failed_temporary_cleanup_invalidates_plan_without_current_source_feedback():
    temporary = SimpleNamespace(installed=True)
    def retire():
        raise ValueError('protected future source changed')
    temporary.retire = retire
    run, clock, ledger, row, result = execute_attempt_finally(temporary=temporary)
    assert run(object()) is None
    assert row['accepted'] is False and row['failed_interfaces'] == []
    assert 'future_head_cleanup_failed' in row['reason']
    assert ledger.remaining('bank') == 28.
