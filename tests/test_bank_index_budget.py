"""Execute the production bank feeder loop with controlled native boundaries."""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'smilepcb/keyboard_routing/conditional_banks.py'


def feeder_loop(namespace):
    tree = ast.parse(SOURCE.read_text())
    loops = [node for node in ast.walk(tree) if isinstance(node, ast.For)
             and isinstance(node.target, ast.Tuple)
             and [getattr(value, 'id', None) for value in node.target.elts] == ['grid', 'allowance']]
    assert len(loops) == 1
    args = ast.arguments(posonlyargs=[], args=[ast.arg(arg=name) for name in
                         ('check', 'selected', 'feeder_ports', 'width', 'stop', 'row')],
                         kwonlyargs=[], kw_defaults=[], defaults=[])
    function = ast.FunctionDef(name='run', args=args, body=[loops[0]], decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(module, str(SOURCE), 'exec'), namespace)
    return namespace['run']


def exercise(*, nets=('gamma', 'alpha', 'beta'), missing=None, constructor_cost=.25,
             route_cost=.1, start=0., stop=100., exception_at=None,
             mutate_before_exception=False, invocations=1):
    clock = SimpleNamespace(now=start)
    clock.monotonic = lambda: clock.now
    state = SimpleNamespace(worlds=[], calls=[], graphs=[], history=[], errors=[],
                            footprints=0, boards=[], rows=[])
    missing = set(nets if missing is None else missing)

    class Board:
        def __init__(self): self.items = ['prefix']
        def GetFootprints(self):
            state.footprints += 1
            return [SimpleNamespace(GetReference=lambda: 'U_MCU', GetLayer=lambda: 31)]

    class Router:
        allow_penalty_fallback = False
        def __init__(self, board, *, deadline, compact, track_width_nm):
            self.board, self.deadline = board, deadline
            self.compact, self.track_width = compact, track_width_nm
            self.added, self.indexed = [], set(board.items)
            state.worlds.append(self)
            clock.now += constructor_cost
        def expired(self): return clock.now >= self.deadline

    def graph(board, selected, deadline):
        state.graphs.append({'board': board, 'at': clock.now, 'deadline': deadline})
        return {'missing': sorted(missing)}

    def route(board, router, selected, *, finer_grid_steps):
        ordinal = len(state.calls)
        state.calls.append({'router': router, 'board': board, 'net': next(iter(selected)),
                            'at': clock.now, 'deadline': router.deadline,
                            'grid': router.grid_step, 'finer': finer_grid_steps,
                            'old_telemetry': {name: deepcopy(getattr(router, name))
                                for name in ('last_search', 'last_plan', 'graph_diagnostic', 'attempt_counts')
                                if hasattr(router, name)},
                            'settings': (router.search_budget_s, router.connection_budget_s,
                                router.search_expansion_limit, router.heuristic_weight,
                                router.via_cost_nm, router.layer_change_heuristic_nm),
                            'penalty': None if router.routing_penalty is None else
                                [router.routing_penalty(SimpleNamespace(GetTotalLength=lambda: 7), layer, 'x', via)
                                 for layer, via in ((31, False), (0, False), (0, True))]})
        if router.expired(): raise TimeoutError('Initial native graph deadline exhausted')
        if exception_at == ordinal and not mutate_before_exception:
            raise TimeoutError('Native graph deadline exhausted before mutation')
        uid = 'added-' + str(ordinal)
        board.items.append(uid); router.added.append(uid); router.indexed.add(uid)
        if exception_at == ordinal:
            raise TimeoutError('Controller source refresh deadline exhausted after emission')
        router.last_search = {'reason': 'connected', 'net': next(iter(selected))}
        router.last_plan = {'emitted': uid}
        router.attempt_counts = {next(iter(selected)): 1}
        router.graph_diagnostic = {'attempts': [{'search': router.last_search}],
                                   'attempt_counts': dict(router.attempt_counts)}
        state.history.append(deepcopy(router.graph_diagnostic))
        clock.now = router.deadline + .01 if route_cost == 'expires' else clock.now + route_cost
        return 1

    run = feeder_loop({'time': clock, 'KeyboardRouter': Router, 'copper_graph': graph,
                       '_pad_state': lambda graph, selected: graph, 'route_components': route})
    for _ in range(invocations):
        board = Board(); row = {'routing_history': [], 'failed_interfaces': []}
        state.boards.append(board); state.rows.append(row)
        try:
            run(board, set(nets), [{'net': net} for net in nets], 150000, stop, row)
        except Exception as error:
            state.errors.append(error)
    state.clock = clock
    return state


def test_one_lazy_router_per_grid_keeps_physical_feeder_order():
    state = exercise()
    assert not state.errors
    assert len(state.worlds) == 2
    assert [(q['grid'], q['net']) for q in state.calls] == [
        (grid, net) for grid in (500000, 125000) for net in ('gamma', 'alpha', 'beta')]
    assert len({id(q['router']) for q in state.calls[:3]}) == 1
    assert len({id(q['router']) for q in state.calls[3:]}) == 1
    assert state.calls[0]['router'] is not state.calls[3]['router']
    assert len(state.graphs) == 2


def test_no_missing_nets_constructs_no_router():
    state = exercise(missing=())
    assert not state.worlds and not state.calls and not state.errors


def test_new_bank_invocation_cannot_reuse_previous_world():
    state = exercise(invocations=2)
    assert not state.errors and len(state.worlds) == 4
    assert {id(q['router']) for q in state.calls[:6]}.isdisjoint(
        {id(q['router']) for q in state.calls[6:]})
    assert all(world.board in state.boards for world in state.worlds)


def test_settings_absolute_deadlines_and_diagnostic_history_stay_per_call():
    state = exercise()
    assert not state.errors
    assert [q['deadline'] for q in state.calls] == pytest.approx([3., 3.35, 3.45, 5.55, 5.9, 6.])
    for call in state.calls:
        coarse = call['grid'] == 500000
        allowance = 2 if coarse else 4
        assert call['settings'] == (allowance, allowance, 200000, 3, 1000000, 0)
        assert call['finer'] == ((125000,) if coarse else ())
        assert call['penalty'] == ([0, 14, 0] if coarse else None)
        assert not call['old_telemetry']
        assert call['at'] < call['deadline'] <= 97.
    assert [q['diagnostic'] for q in state.rows[0]['routing_history']] == state.history


def test_expired_per_net_world_gets_new_deadline_for_next_net():
    state = exercise(route_cost='expires')
    assert not state.errors and len(state.calls) == 6
    assert len(state.worlds) == 2
    assert all(q['at'] < q['deadline'] for q in state.calls)


def test_constructor_expiry_skips_only_unstarted_call_without_certificate():
    state = exercise(nets=('alpha',), constructor_cost=4.)
    assert not state.errors
    assert len(state.calls) == 1 and state.calls[0]['grid'] == 125000
    skipped = state.rows[0]['routing_history'][0]
    assert skipped['connections'] == 0 and skipped['diagnostic']['budget_exhausted']
    assert skipped['diagnostic']['skipped'] is True
    assert not state.rows[0]['failed_interfaces'] and 'feedback_basis' not in state.rows[0]


def test_bank_cutoff_during_constructor_preserves_final_proof_tail():
    state = exercise(constructor_cost=4., stop=6.)
    assert not state.errors and not state.calls
    assert len(state.graphs) == 1 and len(state.worlds) == 1
    assert state.clock.now == 4. < 6.
    assert not state.rows[0]['failed_interfaces']


def test_expired_sweep_does_not_spend_proof_reserve_on_another_graph():
    state = exercise(start=7., stop=10.)
    assert not state.graphs and not state.worlds and not state.calls


@pytest.mark.parametrize('partial', [False, True])
def test_route_timeout_still_escapes_even_after_partial_emission(partial):
    state = exercise(exception_at=1, mutate_before_exception=partial)
    assert len(state.errors) == 1 and isinstance(state.errors[0], TimeoutError)
    assert len(state.calls) == 2 and len(state.rows[0]['routing_history']) == 1
    assert len(state.boards[0].items) == (3 if partial else 2)
    assert not state.rows[0]['failed_interfaces'] and 'feedback_basis' not in state.rows[0]
