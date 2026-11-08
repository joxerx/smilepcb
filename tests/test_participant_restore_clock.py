"""Portable restoration clocks and physical ordering; Native proof remains separate.

The first two cases reuse the preserved restore-clock-candidate regressions.
"""
from types import SimpleNamespace as S
import sys

import pytest
from smilepcb.keyboard_routing import feeder_repair as repair


def board_with_pads(rows):
    footprints = {}
    for uid, net, ref, number, point in rows:
        pad = S(m_Uuid=S(AsString=lambda uid=uid: uid),
                GetNetname=lambda net=net: net, GetNumber=lambda number=number: number,
                GetPosition=lambda point=point: S(x=point[0], y=point[1]))
        footprints.setdefault(ref, []).append(pad)
    values = [S(GetReference=lambda ref=ref: ref, Pads=lambda pads=pads: pads)
              for ref, pads in footprints.items()]
    return S(GetFootprints=lambda: values)


def supply_board():
    return board_with_pads([('a', 'supply', 'A', '1', (0, 0)),
                            ('b', 'supply', 'B', '1', (10, 0))])


def test_restoration_search_cannot_spend_its_own_partition_proof(monkeypatch):
    clock = S(now=0., joined=False); routers = []
    monkeypatch.setattr(repair, 'time', S(monotonic=lambda: clock.now))
    def partitions(board, deadline):
        if clock.now >= deadline: raise TimeoutError('No partition proof time')
        return [{'a', 'b'}] if clock.joined else [{'a'}, {'b'}]
    def route(board, router, nets, **kw):
        assert nets == {'supply'} and kw['required_pad_ids'] == {'a', 'b'}
        clock.now = router.deadline; clock.joined = True; router.added = ['native-route']; return 1
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.completion', S(pad_partitions=partitions))
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.native_graph', S(route_components=route))
    def factory(board, **kw):
        router = S(**kw, added=[]); routers.append(router); return router
    rows, created = repair._restore_participant_partitions(supply_board(), [{'a', 'b'}], {'supply'},
        {'a': 'supply', 'b': 'supply'}, set(), factory, deadline=22.)
    assert rows[0]['restored'] and rows[0]['partition_proof_checked'] and created == {'native-route'}
    assert routers[0].deadline < 22. and clock.now < 22.


def test_early_search_failure_keeps_its_diagnostics_without_claiming_proof(monkeypatch):
    clock = S(now=0.); history = []
    monkeypatch.setattr(repair, 'time', S(monotonic=lambda: clock.now))
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.completion', S(pad_partitions=lambda *a: [{'a'}, {'b'}]))
    def route(board, router, nets, **kw):
        clock.now = router.deadline - 1; router.graph_diagnostic = {'expanded': 37}
        raise TimeoutError('search window ended')
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.native_graph', S(route_components=route))
    with pytest.raises(TimeoutError):
        repair._restore_participant_partitions(supply_board(), [{'a', 'b'}], {'supply'},
            {'a': 'supply', 'b': 'supply'}, set(), lambda board, **kw: S(**kw, added=[]), deadline=22., history=history)
    assert history[0]['net'] == 'supply' and not history[0]['partition_proof_checked']
    assert 'restored' not in history[0]
    assert history[0]['history'][0]['graph'] == {'expanded': 37}
    assert history[0]['history'][0]['reason'] == 'search window ended'


def test_successful_search_without_fresh_partition_proof_cannot_claim_restoration(monkeypatch):
    clock = S(now=0.); history = []; reads = []
    monkeypatch.setattr(repair, 'time', S(monotonic=lambda: clock.now))
    def partitions(board, deadline):
        reads.append(deadline)
        if len(reads) > 1: raise TimeoutError('partition evidence unavailable')
        return [{'a'}, {'b'}]
    def route(board, router, nets, **kw):
        router.added = ['unproved-route']; return 1
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.completion', S(pad_partitions=partitions))
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.native_graph', S(route_components=route))
    with pytest.raises(TimeoutError, match='partition evidence unavailable'):
        repair._restore_participant_partitions(supply_board(), [{'a', 'b'}], {'supply'},
            {'a': 'supply', 'b': 'supply'}, set(), lambda board, **kw: S(**kw, added=[]), deadline=22., history=history)
    assert not history[0]['partition_proof_checked'] and 'restored' not in history[0]
    assert history[0]['history'][0]['connections'] == 1
    assert history[0]['history'][0]['reason'] == 'partition evidence unavailable'


def test_uuid_renumbering_and_partition_iteration_do_not_change_physical_restoration_order(monkeypatch):
    orders = []
    for identifiers in (('a0', 'a1', 'z0', 'z1'), ('z0', 'z1', 'a0', 'a1')):
        records = [(identifiers[0], 'shared', 'A', '1', (0, 0)),
                   (identifiers[1], 'shared', 'A', '2', (10, 0)),
                   (identifiers[2], 'shared', 'B', '1', (100, 0)),
                   (identifiers[3], 'shared', 'B', '2', (110, 0))]
        bindings = {uid: (net, ref, number, point) for uid, net, ref, number, point in records}
        groups = [set(identifiers[:2]), set(identifiers[2:])]
        if orders: groups.reverse()
        joined = []; order = []
        def partitions(board, deadline):
            covered = set().union(*joined) if joined else set()
            return joined + [{uid} for uid in identifiers if uid not in covered]
        def route(board, router, nets, **kw):
            assert nets == {'shared'}
            ids = set(kw['required_pad_ids']); order.append(sorted(bindings[uid] for uid in ids)); joined.append(ids)
            return 1
        monkeypatch.setattr(repair, 'time', S(monotonic=lambda: 0.))
        monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.completion', S(pad_partitions=partitions))
        monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.native_graph', S(route_components=route))
        result, _ = repair._restore_participant_partitions(board_with_pads(records), groups, {'shared'},
            {uid: 'shared' for uid in identifiers}, set(), lambda board, **kw: S(**kw, added=[]), deadline=100.)
        assert all(row['restored'] for row in result)
        orders.append(order)
    assert orders[0] == orders[1]
    assert [group[0][1] for group in orders[0]] == ['A', 'B']


def test_real_repair_timeout_retains_partial_restoration_and_exact_rollback(monkeypatch):
    from test_participant_feeder_recovery import MixedFixture, frame
    fixture = MixedFixture(monkeypatch, fail_return='timeout')
    rows = fixture.run(participant_ownership=fixture.owner_token)
    assert not rows[0]['accepted'] and frame(fixture.board) == fixture.before
    phases = [row for row in rows[0]['restoration']
              if row.get('phase') == 'displaced_partition' and row['net'] == fixture.return_net]
    assert len(phases) == 1 and phases[0]['net'] == fixture.return_net
    assert not phases[0]['partition_proof_checked'] and 'restored' not in phases[0]
    assert phases[0]['history'][0]['reason'] == 'Injected shared recovery deadline'


@pytest.mark.parametrize('joined', [False, True])
def test_search_phase_timeout_spends_reserved_window_on_actual_partition_proof(monkeypatch, joined):
    clock = S(now=0., joined=False); history = []; reads = []
    monkeypatch.setattr(repair, 'time', S(monotonic=lambda: clock.now))
    def partitions(board, deadline):
        assert clock.now < deadline
        reads.append((clock.now, deadline))
        return [{'a', 'b'}] if clock.joined else [{'a'}, {'b'}]
    def route(board, router, nets, **kw):
        assert nets == {'supply'} and kw['required_pad_ids'] == {'a', 'b'}
        clock.now = router.deadline; clock.joined = joined
        router.added = ['actual-route']; router.graph_diagnostic = {'expanded': 37}
        raise TimeoutError('Native search graph refresh cutoff')
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.completion', S(pad_partitions=partitions))
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.native_graph', S(route_components=route))
    rows, created = repair._restore_participant_partitions(supply_board(), [{'a', 'b'}], {'supply'},
        {'a': 'supply', 'b': 'supply'}, set(), lambda board, **kw: S(**kw, added=[]), deadline=22., history=history)
    assert rows is history and rows[0]['partition_proof_checked']
    assert rows[0]['restored'] is joined and created == {'actual-route'}
    assert reads == [(0., 22.), (20., 22.)]
    attempt = rows[0]['history'][0]
    assert attempt['search_timeout'] == 'Native search graph refresh cutoff'
    assert attempt['graph'] == {'expanded': 37} and 'connections' not in attempt
    assert len(rows[0]['history']) == 1


@pytest.mark.parametrize('fail_global_proof', [False, True])
def test_search_phase_timeout_real_caller_still_requires_fresh_global_proof(monkeypatch, fail_global_proof):
    from test_participant_feeder_recovery import MixedFixture, frame
    fixture = MixedFixture(monkeypatch); clock = S(now=0., timed_out=False, proved=False)
    monkeypatch.setattr(repair, 'time', S(monotonic=lambda: clock.now))
    native_graph = sys.modules['smilepcb.keyboard_routing.native_graph']
    completion = sys.modules['smilepcb.keyboard_routing.completion']
    route, partition, graph = native_graph.route_components, completion.pad_partitions, native_graph.copper_graph
    def timeout_after_actual_route(board, router, nets, **kw):
        before = set(board.items)
        result = route(board, router, nets, **kw)
        if fixture.return_net in nets:
            router.added = list(set(board.items) - before)
            clock.now = router.deadline; clock.timed_out = True
            raise TimeoutError('Native search graph refresh cutoff')
        return result
    def prove_partition(board, deadline):
        current = partition(board, deadline)
        if clock.timed_out:
            assert clock.now < deadline
            clock.proved = True
        return current
    def prove_global(board, nets, deadline):
        if clock.timed_out:
            assert clock.proved, 'Global proof cannot replace the reserved actual partition read'
            if fail_global_proof: raise TimeoutError('Global proof unavailable')
        return graph(board, nets, deadline)
    monkeypatch.setattr(native_graph, 'route_components', timeout_after_actual_route)
    monkeypatch.setattr(native_graph, 'copper_graph', prove_global)
    monkeypatch.setattr(completion, 'pad_partitions', prove_partition)
    rows = fixture.run(participant_ownership=fixture.owner_token)
    assert clock.proved and rows[0]['accepted'] is not fail_global_proof
    phase = next(row for row in rows[0]['restoration']
                 if row.get('phase') == 'displaced_partition' and row['net'] == fixture.return_net)
    assert phase['partition_proof_checked'] and phase['restored']
    assert phase['history'][0]['search_timeout'] == 'Native search graph refresh cutoff'
    if fail_global_proof:
        assert rows[0]['reason'] == 'Global proof unavailable' and 'after' not in rows[0]
        assert frame(fixture.board) == fixture.before
    else:
        assert rows[0]['after'] == {} and rows[0]['contacts_preserved']
