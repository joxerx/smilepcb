"""Host column transaction/budget checks; native fixtures cover real geometry."""
import ast
from copy import deepcopy
import math
from pathlib import Path
import statistics
import sys
from types import ModuleType, SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'smilepcb/keyboard_routing/field.py'


def exercise(monkeypatch, *, counts=(3, 2), deadline=100., failures=None,
             external_access=False, access_adds=True, finite=False,
             expire_selection=False, invalid_plan=False):
    """Run the production coordinator with deterministic native boundaries."""
    clock = SimpleNamespace(now=0.)
    state = SimpleNamespace(created=[], calls=[], commits=[], access_calls=[], error=None,
                            report=None, inventory=SimpleNamespace(
                                ports={'original': {'net': 'fixed'}},
                                resolved={'resolved-original'}, repair_reserved={'held-original'}))
    state.initial_inventory = deepcopy(vars(state.inventory))
    failures = failures or {}
    serial = 0

    class Track:
        def __init__(self, net, start, end):
            nonlocal serial
            self.uid = 'item-' + str(serial)
            serial += 1
            self.net, self.start, self.end = net, tuple(start), tuple(end)
            self.m_Uuid = SimpleNamespace(AsString=lambda: self.uid)

        def GetClass(self): return 'PCB_TRACK'
        def GetStart(self): return SimpleNamespace(x=self.start[0], y=self.start[1])
        def GetEnd(self): return SimpleNamespace(x=self.end[0], y=self.end[1])
        def GetPosition(self): return self.GetStart()

    class Via(Track):
        def GetClass(self): return 'PCB_VIA'

    class Pad:
        def __init__(self, net, column, index, on_layer=True):
            self.net, self.position = net, (column * 10000000, index * 5000000)
            self.on_layer, self.ref = on_layer, net + '-' + str(index)
        def GetPosition(self): return SimpleNamespace(x=self.position[0], y=self.position[1])
        def GetNetname(self): return self.net
        def IsOnLayer(self, layer): return self.on_layer
        def GetNumber(self): return '1'
        def GetParentFootprint(self): return SimpleNamespace(GetReference=lambda: self.ref)

    columns = {f'column-{i}': [Pad(f'column-{i}', i, j,
                    not (external_access and i == 1)) for j in range(count)]
               for i, count in enumerate(counts)}
    items = [Track('fixed', (-10000000, 0), (-10000000, 1000000))]
    reused = {}
    if external_access and not access_adds:
        for pad in columns['column-1']:
            item = Via(pad.net, pad.position, pad.position)
            items.append(item)
            reused[pad.ref] = item

    class Board:
        def GetDesignSettings(self): return SimpleNamespace(m_TrackMinWidth=150000)
        def GetTracks(self): return list(items)
        def Delete(self, item): items.remove(item)
        def BuildConnectivity(self): pass

    board = Board()
    state.board = board
    snapshot = lambda board: {'footprints': {}, 'tracks': {
        t.uid: (t.net, t.start, t.end, t.GetClass()) for t in board.GetTracks()}}
    state.entry = snapshot(board)
    state.snapshot = lambda: snapshot(board)

    class Router:
        def __init__(self, board, *, deadline, **kwargs):
            self.board, self.deadline, self.track_width = board, deadline, kwargs['track_width_nm']
            self.generation = SimpleNamespace(name='native')  # This fixture isolates the original clock/rollback path.
            self.added, self.path_policy = [], None
            state.created.append(self)
            clock.now += .25
        def expired(self): return clock.now >= self.deadline
        def clear(self, shape, layer, net, via=False):
            if expire_selection: clock.now = deadline + .01
            return finite
        def add_path(self, path, layer, net):
            for a, b in zip(path, path[1:]):
                item = Track(net, a, b)
                items.append(item)
                self.added.append(item.uid)
        def add_via(self, position, net):
            item = Via(net, position, position)
            items.append(item)
            self.added.append(item.uid)

    class KeyboardRouter(Router):
        def connect(self, source, target):
            ordinal = len(state.calls)
            state.calls.append({'router': self, 'at': clock.now, 'deadline': self.deadline,
                'net': source.net, 'last_search_present': hasattr(self, 'last_search'),
                'last_plan_present': hasattr(self, 'last_plan'),
                'settings': (self.grid_step, self.connection_budget_s, self.heuristic_weight),
                'search_budget_present': hasattr(self, 'search_budget_s')})
            self.last_search, self.last_plan = {'old': ordinal}, {'old': ordinal}
            self.add_path([source.position, target.position], source.layer, source.net)
            clock.now += .4
            outcome = failures.get(ordinal, 'success')
            if outcome != 'success':
                state.inventory.ports['speculative'] = {'net': source.net}
                state.inventory.resolved.add('speculative')
                state.inventory.repair_reserved.add('speculative')
            if outcome == 'exception': raise RuntimeError('native branch exception')
            return outcome == 'success'

    def access(board, pads, **kwargs):
        state.access_calls.append(len(pads))
        ports = []
        for pad in pads:
            if access_adds:
                item = Via(pad.net, pad.position, pad.position)
                items.append(item)
            else:
                item = reused[pad.ref]
            ports.append({'ref': pad.ref, 'pin': '1', 'connected': True,
                          'via_id': item.uid, 'ids': [item.uid] if access_adds else []})
        return ports

    def commit(router, plan, net):
        state.commits.append({'router': router, 'deadline': router.deadline, 'at': clock.now})
        if router.expired(): raise ValueError('Matrix plan has incompatible copper or vias')
        if invalid_plan: raise ValueError('invalid finite geometry')
        for path, layer in plan['paths']: router.add_path(path, layer, net)

    def module(name, **values):
        value = ModuleType(name)
        vars(value).update(values)
        monkeypatch.setitem(sys.modules, name, value)

    module('smilepcb.native_worker', snapshot=snapshot)
    module('smilepcb.keyboard_routing.power', seed_plane_access=access)
    module('smilepcb.keyboard_routing.safety', require_via_land_clearance=lambda *a, **k: None)
    module('smilepcb.keyboard_routing.interface_launches',
           current_launch_inventory=lambda: state.inventory)
    module('smilepcb.keyboard_routing.field_policy', FieldPathPolicy=lambda role: role)
    module('smilepcb.keyboard_routing.field_geometry', row_domain=lambda *a: (0, 1),
           column_paths=lambda a, b: [[a, b]], horizontal_length=lambda path: 0)
    module('smilepcb.keyboard_routing.matrix', KeyboardRouter=KeyboardRouter)
    module('smilepcb.keyboard_routing.native_graph',
           Endpoint=lambda position, layer, net: SimpleNamespace(position=position, layer=layer, net=net))
    node = next(q for q in ast.parse(SOURCE.read_text()).body
                if isinstance(q, ast.FunctionDef) and q.name == '_seed_groups')
    env = {'__name__': 'smilepcb.keyboard_routing.field',
           '__package__': 'smilepcb.keyboard_routing', 'math': math, 'statistics': statistics,
           'time': SimpleNamespace(monotonic=lambda: clock.now),
           'p': SimpleNamespace(SHAPE_SEGMENT=lambda a, b, width: (a, b), PCB_VIA=Via),
           'vec': lambda q: q, 'xy': lambda q: (q.x, q.y), 'LocalRouter': Router,
           'axial_path': lambda *a, **k: None, 'commit_plan': commit}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), env)
    try:
        state.report = env['_seed_groups'](board, rows={}, columns=columns, row_key_y={},
                                          row_layer=2, column_layer=0, deadline=deadline)
    except Exception as error:
        state.error = error
    return state


def test_columns_and_fallbacks_share_one_append_only_router(monkeypatch):
    state = exercise(monkeypatch)
    assert state.error is None
    assert all(row['complete'] for row in state.report['columns'])
    assert len(state.created) == 1
    assert len(state.calls) == 3
    assert all(call['router'] is state.created[0] for call in state.calls)


@pytest.mark.parametrize('deadline', [100., 2.75])
def test_fallback_deadline_is_scoped_and_never_renews_shared_budget(monkeypatch, deadline):
    state = exercise(monkeypatch, deadline=deadline)
    assert state.error is None
    assert len(state.calls) == 3
    assert all(call['deadline'] == min(deadline, call['at'] + 3) for call in state.calls)
    assert all(router.deadline == deadline for router in state.created)


def test_fallbacks_clear_previous_search_diagnostics_and_keep_settings(monkeypatch):
    state = exercise(monkeypatch)
    assert state.error is None and len(state.calls) == 3
    assert all(not call['last_search_present'] and not call['last_plan_present'] for call in state.calls)
    assert all(call['settings'] == (250000, 2, 3) for call in state.calls)
    # Direct connect has a separate search budget; preserve its existing default.
    assert not any(call['search_budget_present'] for call in state.calls)


@pytest.mark.parametrize('access_adds', [True, False])
def test_nonempty_external_access_invalidates_even_when_reusing_a_via(monkeypatch, access_adds):
    state = exercise(monkeypatch, counts=(2, 2), external_access=True, access_adds=access_adds)
    assert state.error is None
    assert all(row['complete'] for row in state.report['columns'])
    assert state.access_calls == [0, 2]
    assert len(state.created) == 2
    assert state.calls[0]['router'] is not state.calls[1]['router']


def test_failed_column_discards_its_index_copper_and_launch_changes(monkeypatch):
    state = exercise(monkeypatch, failures={1: 'failure'})
    assert state.error is None
    assert [row['complete'] for row in state.report['columns']] == [False, True]
    assert state.calls[0]['router'] is state.calls[1]['router']
    assert state.calls[1]['router'] is not state.calls[2]['router']
    assert all(call['router'].deadline == 100. for call in state.calls)
    assert not any(item.net == 'column-0' for item in state.board.GetTracks())
    assert vars(state.inventory) == state.initial_inventory


def test_exception_restores_column_transaction_and_fallback_deadline(monkeypatch):
    state = exercise(monkeypatch, counts=(2,), failures={0: 'exception'})
    assert isinstance(state.error, RuntimeError)
    assert str(state.error) == 'native branch exception'
    assert state.snapshot() == state.entry
    assert vars(state.inventory) == state.initial_inventory
    assert all(router.deadline == 100. for router in state.created)


def test_expired_selected_finite_plan_reports_budget_without_committing(monkeypatch):
    state = exercise(monkeypatch, counts=(2,), finite=True, expire_selection=True)
    assert state.error is None
    assert not state.report['columns'][0]['complete']
    assert state.report['columns'][0]['reason'] == 'unresolved_budget'
    assert not state.commits
    assert state.snapshot() == state.entry


def test_nonexpired_invalid_finite_geometry_still_raises(monkeypatch):
    state = exercise(monkeypatch, counts=(2,), finite=True, invalid_plan=True)
    assert isinstance(state.error, ValueError)
    assert str(state.error) == 'invalid finite geometry'
    assert len(state.commits) == 1 and state.commits[0]['at'] < state.commits[0]['deadline']
    assert state.snapshot() == state.entry
