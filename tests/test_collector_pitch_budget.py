"""Controlled-clock pitch transactions; native probes own physical admission."""
import ast
from collections import deque
from copy import deepcopy
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
from collector_partition_support import Board as PartitionBoard, capacity

ROOT = Path(__file__).resolve().parents[1]


def exercise(monkeypatch, mode='timeout', *, deadline=3., orchestration=False):
    clock = SimpleNamespace(now=0.)
    clock.monotonic = lambda: clock.now
    state = SimpleNamespace(emitted=0, adds=[], policies=[], searches=[], published=[],
                            transitions=0, retried=False, report=None, error=None)
    inventory = SimpleNamespace(ports={'prior': {'net': 'prior', 'exit_nm': (7, 9)}},
                                resolved={'prior-resolved'}, repair_reserved={'prior-reserved'})
    state.inventory = inventory
    state.initial_inventory = deepcopy(vars(inventory))
    ports = [{'net': net, 'exit_nm': point, 'exit_layer': 0, 'heading_origin_nm': (-1, point[1]),
              'terminal_id': net + '-original', 'added_ids': [net + '-original']}
             for net, point in [('alpha', (0, 0)), ('beta', (0, 200000))]]
    state.ports = ports
    state.original_ports = deepcopy(ports)

    class Track:
        def __init__(self, uid):
            self.uid = uid
            self.m_Uuid = SimpleNamespace(AsString=lambda: uid)

    # The transaction must retain work accepted before this compression call.
    tracks = [Track('source-prefix'), Track('prior-collector')]

    class Board:
        def GetTracks(self): return list(tracks)
        def Delete(self, item): tracks.remove(item)
        def BuildConnectivity(self): pass
        def GetDesignSettings(self): return PartitionBoard().GetDesignSettings()

    board = Board()
    state.ids = lambda: tuple(t.uid for t in tracks)
    state.initial_ids = state.ids()

    class Policy:
        def __init__(self, board, live, **kwargs):
            self.ports = deepcopy(live)
            self.resolved = set(inventory.resolved)
            self.repair_reserved = set(inventory.repair_reserved)
            state.policies.append(self)
        def accepts_segment(self, *args):
            if mode == 'final_timeout' and state.emitted == 2:
                clock.now = deadline
            return mode != 'invalid'

    class Router:
        def __init__(self, board, *, deadline, track_width_nm, **kwargs):
            self.deadline = deadline
            self.track_width = track_width_nm
            self.minimum_clearance, self.via_width = 100000, 600000
            self.net_clearances, self.added = {}, []
            self.port_policy = None
        def expired(self): return clock.now >= self.deadline
        def clear(self, *args, **kwargs): return True
        def add_path(self, path, layer, net):
            state.adds.append({'net': net, 'time': clock.now,
                              'inventory': deepcopy(vars(inventory)),
                              'resolved': set(self.port_policy.resolved)})
            if self.expired():
                raise TimeoutError('Controller source refresh deadline exhausted')
            if mode == 'retry' and state.emitted == 1 and not state.retried:
                state.retried = True
                self.port_policy.resolved.add('stale-local-policy')
                raise ValueError('Controlled native geometry rejection')
            uid = 'pitch-' + str(state.emitted)
            tracks.append(Track(uid))
            self.added.append(uid)
            state.emitted += 1
            inventory.ports['speculative'] = {'net': net}
            inventory.resolved.add('speculative')
            inventory.repair_reserved.add('speculative')
            self.port_policy.resolved.add('speculative')
            if mode == 'timeout' and state.emitted == 1:
                clock.now = self.deadline
            if mode == 'exception' and state.emitted == 1:
                raise RuntimeError('Unexpected native mutation failure')

    def transition(starts, axis, sign, *args):
        state.transitions += 1
        return [[start, (1000000, start[1])] for start in starts]

    launch_module = ModuleType('smilepcb.keyboard_routing.interface_launches')
    launch_module.current_launch_inventory = lambda: inventory
    monkeypatch.setitem(sys.modules, launch_module.__name__, launch_module)
    ns = {'__name__': 'smilepcb.keyboard_routing.collector_peel',
          '__package__': 'smilepcb.keyboard_routing', 'math': math, 'time': clock,
          'deepcopy': deepcopy, 'LocalRouter': Router, 'PortPolicy': Policy,
          'current_launch_inventory': lambda: inventory,
          'pitch_transition': transition, 'heading': lambda q: (1, 0),
          'vec': lambda q: q, 'clean_port_path': lambda q: q,
          'p': SimpleNamespace(SHAPE_SEGMENT=lambda *args: args)}
    path = ROOT / 'smilepcb/keyboard_routing/collector_peel.py'
    tree = ast.parse(path.read_text())
    bodies = [n for n in tree.body if isinstance(n, ast.FunctionDef)
              or isinstance(n, ast.ClassDef) and n.name == 'Style']
    exec(compile(ast.Module(body=bodies, type_ignores=[]), str(path), 'exec'), ns)
    if deadline <= 0:
        clock.now = 0.

    if orchestration:
        ns['partition_initial'] = capacity(clock)['partition_initial']
        def snapshot(board):
            return {'tracks': {t.uid: t.uid for t in tracks}, 'footprints': {'fixed': 'same'}}
        class Search:
            def __init__(self, board, live, nets, targets, *, deadline, goals):
                state.searches.append({'ports': deepcopy(live), 'deadline': deadline,
                                       'at': clock.now, 'ids': state.ids()})
                self.deadline, self.branch, self.interface_graph = deadline, None, None
            def solve(self):
                clock.now = self.deadline
                return {'accepted': False, 'reason': 'Controlled search exhaustion'}
        ns.update(deque=deque, snapshot=snapshot,
                  field_targets=lambda *args: {}, groups_for=lambda *args: [['alpha', 'beta']],
                  branch_goals=lambda *args: [SimpleNamespace(point=(2000000, 0))],
                  CollectorSearch=Search,
                  copper_graph=lambda *args: {'nodes': [], 'find': lambda i: i},
                  publish_launches=lambda live, *args: state.published.append(deepcopy(live)))
        ns['p'].PAD = type('Pad', (), {})
        caller = ROOT / 'smilepcb/keyboard_routing/collectors.py'
        functions = [n for n in ast.parse(caller.read_text()).body if isinstance(n, ast.FunctionDef)]
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(caller), 'exec'), ns)
        operation = lambda: ns['route_collectors'](board, {}, ports, deadline=6.)
    else:
        operation = lambda: ns['compress'](board, ports, ['alpha', 'beta'], deadline=deadline)
    try:
        state.report = operation()
    except Exception as error:
        state.error = error
    state.clock = clock
    return state


def assert_restored(state):
    assert state.ids() == state.initial_ids
    assert vars(state.inventory) == state.initial_inventory
    assert state.ports == state.original_ports


def test_expired_pitch_budget_skips_geometry_and_returns_original_ports(monkeypatch):
    state = exercise(monkeypatch, deadline=0.)
    assert state.error is None
    live, report = state.report
    assert not report['accepted'] and report['budget_exhausted']
    assert live == state.original_ports and not state.adds and state.transitions == 0
    assert_restored(state)


def test_timeout_after_one_lane_rolls_back_copper_and_all_launch_state(monkeypatch):
    state = exercise(monkeypatch)
    assert state.error is None
    live, report = state.report
    assert not report['accepted'] and report['budget_exhausted']
    assert 'budget' in report['reason'].lower()
    assert live == state.original_ports and state.emitted == 1 and state.transitions == 1
    assert_restored(state)


def test_timeout_during_final_departure_check_cannot_accept_late_geometry(monkeypatch):
    state = exercise(monkeypatch, 'final_timeout')
    assert state.error is None
    live, report = state.report
    assert state.emitted == 2 and not report['accepted'] and report['budget_exhausted']
    assert live == state.original_ports
    assert_restored(state)


def test_unexpected_exception_rolls_back_then_propagates(monkeypatch):
    state = exercise(monkeypatch, 'exception')
    assert isinstance(state.error, RuntimeError)
    assert str(state.error) == 'Unexpected native mutation failure'
    assert state.emitted == 1
    assert_restored(state)


def test_nonexpired_native_geometry_rejection_remains_failure(monkeypatch):
    state = exercise(monkeypatch, 'invalid')
    assert state.error is None
    live, report = state.report
    assert not report['accepted'] and not report.get('budget_exhausted', False)
    assert live == state.original_ports and state.emitted == 0
    assert_restored(state)


def test_geometric_retry_starts_with_fresh_launch_state_and_policy(monkeypatch):
    state = exercise(monkeypatch, 'retry')
    assert state.error is None and state.report[1]['accepted']
    retried_first_lane = [q for q in state.adds if q['net'] == 'alpha'][1]
    assert retried_first_lane['inventory'] == state.initial_inventory
    assert retried_first_lane['resolved'] == state.initial_inventory['resolved']
    assert 'pitch-0' not in state.ids()
    assert set(state.initial_ids) <= set(state.ids())
    assert state.ports == state.original_ports


def test_nonexpired_valid_pitch_still_accepts_and_advances_ports(monkeypatch):
    state = exercise(monkeypatch, 'success')
    assert state.error is None
    live, report = state.report
    assert report['accepted'] and state.emitted == 2
    assert all(q['terminal_id'].startswith('pitch-') for q in live)
    assert set(state.initial_ids) <= set(state.ids())
    assert state.ports == state.original_ports


def test_pitch_subdeadline_returns_report_and_original_heads_to_collector_search(monkeypatch):
    state = exercise(monkeypatch, orchestration=True)
    assert state.error is None
    report = state.report
    pitch = next(q for q in report['history'] if q['kind'] == 'pitch')
    assert not pitch['accepted'] and pitch['budget_exhausted']
    assert len(state.searches) == 1
    search = state.searches[0]
    assert search['ports'] == state.original_ports and search['ids'] == state.initial_ids
    assert search['at'] == 3. and search['deadline'] == 5.
    assert not report['accepted'] and report['ports'] == state.original_ports
    assert report['added_ids'] == [] and state.clock.now == 5.
    assert_restored(state)
