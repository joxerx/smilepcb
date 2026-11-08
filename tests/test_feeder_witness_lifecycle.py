"""Real repair coordinator with endpoint-graph/native boundary substitutes.

These host controls prove witness/guard/rollback lifecycle, not clearance or
native routing feasibility. Native continuation is a separate qualification.
"""
from copy import deepcopy
from types import SimpleNamespace
import sys

import pytest

from smilepcb.keyboard_routing import feeder_repair as repair
from smilepcb.keyboard_routing.interface_launches import (
    current_launch_inventory, interface_identity, reserve_launches,
    resolved_interfaces,
)
from test_owned_feeder_transaction import NativeStateSeams


class Track(NativeStateSeams.Track):
    def GetLength(self): return 1000000
    def GetClass(self): return self.data['class']


class Pad(Track):
    def GetNumber(self): return '1'
    def GetNetname(self): return self.data['net']
    def GetPosition(self): return SimpleNamespace(x=self.data['start'][0], y=self.data['start'][1])


class Board(NativeStateSeams.Board):
    def GetDesignSettings(self): return SimpleNamespace(m_TrackMinWidth=150000)
    def GetFootprints(self):
        return [SimpleNamespace(GetReference=lambda: 'package', Pads=lambda: self.pads)]


def frame(board):
    return {'tracks': {u: deepcopy(t.data) for u, t in board.items.items()},
            'footprints': {'package': {'pads': [dict(uuid=p.uid, net=p.data['net'])
                                              for p in board.pads]}}}


def endpoint_graph(board, nets, deadline):
    """Connectivity derived from live objects; no scripted missing-net answer."""
    items = [q for q in board.pads + board.GetTracks() if q.data['net'] in nets]
    parent = list(range(len(items)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    nodes = []
    for i, item in enumerate(items):
        points = {tuple(item.data['start']), tuple(item.data['end'])}
        nodes.append({'item': item, 'net': item.data['net'], 'points': points})
        for j, other in enumerate(nodes[:-1]):
            if item.data['net'] == other['net'] and points & other['points']:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(items)): groups.setdefault(find(i), []).append(i)
    return {'nodes': nodes, 'groups': groups, 'find': find, 'nets': set(nets)}


def partitions(board, deadline):
    graph = endpoint_graph(board, {p.data['net'] for p in board.pads}, deadline)
    return [frozenset(graph['nodes'][i]['item'].uid for i in indices
                      if isinstance(graph['nodes'][i]['item'], Pad))
            for indices in graph['groups'].values()
            if any(isinstance(graph['nodes'][i]['item'], Pad) for i in indices)]


class Fixture:
    def __init__(self, monkeypatch, *, guarded=False, fail=None):
        self.board = Board([]); self.board.pads = []; self.ports = []
        self.nets = {'target'} | {'incumbent-%d' % i for i in range(14)}
        self.calls = []; self.strict_calls = []; self.graphs = []
        self.guarded, self.fail = guarded, fail
        self.owner = 'incumbent-0'
        self.protected = set()
        def item(uid, net, a, b, cls=Track):
            value = cls(uid, {'class': 'PAD' if cls is Pad else 'PCB_TRACK',
                             'net': net, 'locked': False, 'start': a, 'end': b,
                             'width': 150000, 'layer': 0})
            (self.board.pads if cls is Pad else []).append(value) if cls is Pad else self.board.Add(value)
            return value
        self.item = item
        def lane(net, row, connected, port):
            y = row * 2000000
            item(net+'-pad', net, (0,y), (0,y), Pad)
            item(net+'-field-pad', net, (3000000,y), (3000000,y), Pad)
            item(net+'-source', net, (0,y), (1000000,y))
            item(net+'-tail', net, (2000000,y), (3000000,y))
            self.protected.update((net+'-source', net+'-tail'))
            if connected: item(net+'-cut', net, (1000000,y), (2000000,y))
            if port:
                self.ports.append({'net': net, 'pad_ids': [net+'-pad'],
                                   'added_ids': [net+'-source'], 'exit_nm': (1000000,y)})
        lane('target', 0, False, guarded)
        for i in range(14): lane('incumbent-%d' % i, i+1, True, guarded and i==0)
        for i in range(18): lane('foreign-%d' % i, i+20, True, True)
        self.before = frame(self.board)
        self.before_parts = partitions(self.board, 100)
        self.queries = []
        class Policy:
            def __init__(policy, board, ports): policy.resolved = set()
            def set_resolved(policy, values): policy.resolved = set(values)
        class Router:
            def __init__(router, board, **kwargs):
                router.board = board; router.deadline = kwargs['deadline']; router.graph_diagnostic = {}
        def graph(board, nets, deadline):
            self.graphs.append(frozenset(board.items))
            return endpoint_graph(board, nets, deadline)
        native = SimpleNamespace(p=SimpleNamespace(PAD=Pad, KIID=lambda uid: uid), snapshot=frame)
        monkeypatch.setitem(sys.modules, 'smilepcb.native_worker', native)
        monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.matrix', SimpleNamespace(KeyboardRouter=Router))
        monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.native_graph', SimpleNamespace(
            copper_graph=graph, negotiate_routes=self.negotiate, route_components=self.route))
        monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.completion', SimpleNamespace(pad_partitions=partitions))
        monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.port_policy', SimpleNamespace(PortPolicy=Policy))
        monkeypatch.setattr(repair, 'time', SimpleNamespace(monotonic=lambda: 0.0))

    def negotiate(self, board, missing, protected, deadline, *, router_factory):
        router = router_factory(board, deadline=deadline)
        scope = current_launch_inventory()
        self.calls.append({'ids': set(board.items), 'resolved': set(scope.resolved),
                           'policy': set(router.port_policy.resolved)})
        removed = []
        if len(self.calls) == 1:
            uid = self.owner+'-cut'; board.Delete(board.items[uid]); removed.append(uid)
        uid = 'witness-%d' % len(self.calls)
        self.item(uid, 'target', (1000000,0), (2000000,0))
        return {'connected': 1, 'removed': removed, 'added': [uid]}

    def route(self, board, router, requested, **kwargs):
        self.strict_calls.append(set(requested))
        if self.fail == 'exception': raise RuntimeError('injected strict-route failure')
        if self.fail == 'reject': return 0
        if self.owner in requested:
            self.item('owner-restored', self.owner, (1000000,2000000), (2000000,2000000))
            return 1
        # The strict target family cannot rediscover the already-valid witness.
        return 0

    def run(self, **options):
        graph = endpoint_graph(self.board, self.nets | {q['net'] for q in self.ports}, 100)
        resolved = resolved_interfaces(self.ports, set(), graph)
        with reserve_launches(self.ports, resolved=resolved,
                              repair_reserved={interface_identity(self.ports[-1])}):
            self.scope_before = deepcopy(current_launch_inventory().__dict__)
            try:
                return repair.repair_feeders(self.board, self.nets, self.protected, deadline=100, **options)
            finally:
                self.scope_after = deepcopy(current_launch_inventory().__dict__)


def test_valid_witness_survives_carve_with_fourteen_incumbents_and_foreign_guards(monkeypatch):
    f = Fixture(monkeypatch)
    history = f.run()
    assert history[0]['accepted'] is True
    assert history[0]['before'] == {'target': 1} and history[0]['after'] == {}
    assert 'witness-1' in f.board.items
    assert 'witness-1' not in f.board.deleted
    assert len(f.calls) == 1 and f.strict_calls == [{f.owner}]
    assert repair._pad_state(endpoint_graph(f.board, f.nets, 100), f.nets)['missing'] == {}
    assert f.scope_after == f.scope_before
    assert all(frame(f.board)['tracks'][u] == q for u, q in f.before['tracks'].items()
               if q['net'].startswith('foreign-'))


def test_reopened_guard_discards_old_witness_and_reactivates_target_before_replan(monkeypatch):
    f = Fixture(monkeypatch, guarded=True)
    history = f.run()
    assert history[0]['accepted'] is True
    assert len(f.calls) == 2
    assert 'witness-1' in f.board.deleted and 'witness-1' not in f.calls[1]['ids']
    assert 'witness-2' in f.board.items and 'witness-2' not in f.board.deleted
    assert ('target-pad',) not in f.calls[1]['resolved']
    assert (f.owner+'-pad',) not in f.calls[1]['resolved']
    assert f.calls[1]['policy'] == f.calls[1]['resolved']
    assert f.strict_calls == [{f.owner}]
    assert f.scope_after['ports'] == f.scope_before['ports']
    assert f.scope_after['repair_reserved'] == f.scope_before['repair_reserved']
    assert ('target-pad',) in f.scope_after['resolved']


@pytest.mark.parametrize('fail', ['reject', 'exception'])
def test_failed_restoration_rolls_back_copper_and_all_launch_fields(monkeypatch, fail):
    f = Fixture(monkeypatch, fail=fail)
    if fail == 'exception':
        with pytest.raises(RuntimeError, match='injected strict-route failure'): f.run()
    else:
        history = f.run()
        assert history[0]['accepted'] is False
        assert history[0]['retained_pending'] == {'target': 1}
    assert frame(f.board) == f.before
    assert partitions(f.board, 100) == f.before_parts
    assert f.scope_after == f.scope_before


def test_repair_retires_isolated_unused_copper_after_restoring_pad_contacts(monkeypatch):
    f = Fixture(monkeypatch)
    old_cut = f.owner + '-cut'
    f.board.Delete(f.board.items[old_cut])
    f.item(old_cut, f.owner, (1000000, 2000000), (1500000, 2000000))
    f.item(f.owner+'-rest', f.owner, (1500000, 2000000), (2000000, 2000000))
    f.item('old-branch', f.owner, (1500000, 2000000), (1500000, 2250000))
    native_graph = sys.modules['smilepcb.keyboard_routing.native_graph']
    negotiate = native_graph.negotiate_routes

    def carve(board, missing, protected, deadline, **kwargs):
        plan = negotiate(board, missing, protected, deadline, **kwargs)
        rest = f.owner+'-rest'
        board.Delete(board.items[rest])
        f.item('carved-fragment', f.owner, (1500000, 2000000), (1600000, 2000000))
        plan['removed'].append(rest)
        plan['retained_fragments'] = ['carved-fragment']
        plan['fragment_sources'] = {'carved-fragment': rest}
        return plan

    monkeypatch.setattr(native_graph, 'negotiate_routes', carve)
    route = native_graph.route_components

    def detour(board, router, requested, **kwargs):
        if f.owner in requested:
            f.item('owner-restored-1', f.owner, (1000000, 2000000), (1000000, 1700000))
            f.item('owner-restored-2', f.owner, (1000000, 1700000), (2000000, 1700000))
            f.item('owner-restored-3', f.owner, (2000000, 1700000), (2000000, 2000000))
            return 1
        return route(board, router, requested, **kwargs)

    monkeypatch.setattr(native_graph, 'route_components', detour)
    entry = frame(f.board)
    history = f.run()
    assert history[0]['accepted']
    assert set(history[0]['retired_feeder_ids']) == {'old-branch', 'carved-fragment'}
    assert repair._pad_state(endpoint_graph(f.board, f.nets, 100), f.nets)['missing'] == {}
    assert all(frame(f.board)['tracks'][uid] == entry['tracks'][uid] for uid in f.protected)
    assert f.scope_after == f.scope_before


@pytest.mark.parametrize('targeted', [False, True])
def test_retries_rejected_multi_net_plan_as_single_targets(monkeypatch, targeted):
    f = Fixture(monkeypatch)
    other = 'other-target'
    f.nets.add(other)
    f.item(other+'-pad', other, (0, -2000000), (0, -2000000), Pad)
    f.item(other+'-field', other, (3000000, -2000000), (3000000, -2000000), Pad)
    f.item(other+'-start', other, (0, -2000000), (1000000, -2000000))
    f.item(other+'-end', other, (2000000, -2000000), (3000000, -2000000))
    native_graph = sys.modules['smilepcb.keyboard_routing.native_graph']
    calls = []

    def negotiate(board, nets, protected, deadline, **kwargs):
        calls.append((set(nets), deadline))
        if len(nets) != 1:
            return {'connected': 0, 'removed': [], 'added': []}
        net, = nets
        y = -2000000 if net == other else 0
        uid = net+'-witness'
        f.item(uid, net, (1000000, y), (2000000, y))
        return {'connected': 1, 'removed': [], 'added': [uid]}

    monkeypatch.setattr(native_graph, 'negotiate_routes', negotiate)
    if targeted:
        history = f.run(target_order=['target', other])
        assert [nets for nets, _ in calls] == [{'target'}, {other}]
        expected = ['target', other]
    else:
        history = f.run()
        assert [nets for nets, _ in calls] == [{other, 'target'}, {other}, {'target'}]
        assert history[0]['accepted'] is False
        expected = [other, 'target']
    assert all(deadline <= 100 for _, deadline in calls)
    assert [row['single_target'] for row in history if row['accepted']] == expected
    assert repair._pad_state(endpoint_graph(f.board, f.nets, 100), f.nets)['missing'] == {}
    assert f.scope_after == f.scope_before


def test_retry_keeps_copper_of_the_incumbent_that_could_not_be_restored(monkeypatch):
    f = Fixture(monkeypatch)
    monkeypatch.setattr(repair, 'pending_matrix', lambda *a: {'target': 1})
    clock = [0]
    monkeypatch.setattr(repair.time, 'monotonic', lambda: clock[0])
    reopened_ids = {uid for uid, track in frame(f.board)['tracks'].items() if track['net'] == f.owner}
    assert reopened_ids - f.protected
    calls = []

    def trial(board, nets, protected, *, deadline, **kwargs):
        calls.append((set(protected), deadline))
        clock[0] += 5
        if reopened_ids <= protected:
            return [{'before': {'target': 1}, 'after': {}, 'accepted': True}]
        return [{'before': {'target': 1}, 'after': {f.owner: 1},
                 'plan': {'removed': sorted(reopened_ids - f.protected)},
                 'retained_pending': {'target': 1}, 'accepted': False}]

    monkeypatch.setattr(repair, '_repair_feeders', trial)
    history = f.run(target_order=['target'])
    assert len(calls) == 2
    assert calls[0][0] == f.protected
    assert calls[1][0] == f.protected | reopened_ids
    assert calls[0][1] < calls[1][1] <= 100
    assert [row['accepted'] for row in history] == [False, True]
    assert history[1]['protected_incumbents'] == [f.owner]


def test_retry_accumulates_new_blocking_copper_without_repeating_a_failed_world(monkeypatch):
    f = Fixture(monkeypatch)
    monkeypatch.setattr(repair, 'pending_matrix', lambda *a: {'target': 1})
    monkeypatch.setattr(repair.time, 'monotonic', lambda: 0)
    f.item('second-blocker', f.owner, (0, 2000000), (0, 3000000))
    eligible = sorted(uid for uid, track in frame(f.board)['tracks'].items()
                      if track['net'] == f.owner and uid not in f.protected)
    assert len(eligible) >= 2
    calls = []

    def trial(board, nets, protected, *, deadline, **kwargs):
        calls.append(set(protected) - f.protected)
        blocked = set(eligible[:2]) - protected
        if not blocked:
            return [{'before': {'target': 1}, 'after': {}, 'accepted': True}]
        return [{'before': {'target': 1}, 'after': {f.owner: 1},
                 'plan': {'removed': [min(blocked)]},
                 'retained_pending': {'target': 1}, 'accepted': False}]

    monkeypatch.setattr(repair, '_repair_feeders', trial)
    history = f.run(target_order=['target'])
    assert calls == [set(), {eligible[0]}, set(eligible[:2])]
    assert [row['accepted'] for row in history] == [False, False, True]


def test_late_reopen_transitions_to_restoration_before_expired_replan(monkeypatch):
    f = Fixture(monkeypatch, guarded=True)
    clock = [0.0]
    monkeypatch.setattr(repair.time, 'monotonic', lambda: clock[0])
    native_graph = sys.modules['smilepcb.keyboard_routing.native_graph']
    original_negotiate = native_graph.negotiate_routes
    original_route = native_graph.route_components

    def negotiate(board, nets, protected, deadline, **kwargs):
        if f.calls:
            raise TimeoutError('Native graph budget exhausted during follow-up setup')
        result = original_negotiate(board, nets, protected, deadline, **kwargs)
        clock[0] = 29.5  # The shared planning slice ends at 30; restoration has time.
        return result

    def restore(board, router, requested, **kwargs):
        if 'target' in requested:
            f.item('strict-target', 'target', (1000000, 0), (2000000, 0))
            return 1
        return original_route(board, router, requested, **kwargs)

    monkeypatch.setattr(native_graph, 'negotiate_routes', negotiate)
    monkeypatch.setattr(native_graph, 'route_components', restore)
    history = f.run()

    assert any(row['accepted'] for row in history), history
    assert len(f.calls) == 1
    assert history[0]['replan_admission_remaining_s'] == pytest.approx(0.5)
    assert repair._pad_state(endpoint_graph(f.board, f.nets, 100), f.nets)['missing'] == {}
    actual_parts = partitions(f.board, 100)
    assert all(any(group <= after for after in actual_parts) for group in f.before_parts)
    after = frame(f.board)
    assert all(after['tracks'][uid] == f.before['tracks'][uid] for uid in f.protected)
    assert f.scope_after['ports'] == f.scope_before['ports']
    assert f.scope_after['repair_reserved'] == f.scope_before['repair_reserved']
    assert f.scope_after['resolved'] == f.scope_before['resolved'] | {('target-pad',)}
