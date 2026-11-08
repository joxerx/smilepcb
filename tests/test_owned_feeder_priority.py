"""Actual repair portfolio with synthetic graph/route boundaries, not native proof."""
from copy import deepcopy
from contextlib import ExitStack
import unittest
from unittest.mock import patch

from test_owned_feeder_transaction import NativeStateSeams


T, A, B, C, OPEN = 'target', 'owner', 'b', 'c', 'open'
SEED = (T, A, B, C)
REVERSE = (T, C, B, A)
FIRST = (A, T, B, C)
SECOND = (B, T, A, C)
COMBINED = (A, B, T, C)
FRONTIER = (A, C, B, T)
ORDINARY = (T, B, A, C)


class PriorityQueue(unittest.TestCase):
    def fixture(self, mode):
        # Reuse the existing exact-checkpoint/raw-property/launch substitutes.
        f = NativeStateSeams(self, 'priority')
        m, journal, board = f.module, f.journal, f.board
        journal.bank_nets = frozenset({T, A, B, C, OPEN})
        for net in (B, C):
            f.inventory.ports[(net,)] = {'net': net, 'pad_ids': [net], 'added_ids': []}
        journal.ports = deepcopy(f.inventory.ports)
        journal.baseline_seal = m._role_seal(journal)
        template = deepcopy(board.items['owner-old'].data)
        for net in (B, C):
            before = f.snapshot(board)
            board.Add(f.Track(net + '-old', dict(template, net=net)))
            m.record_feeder_route(board, journal, before, [net + '-old'], net)
        journal.witnesses[T] = {uid: {'net': shape['net'], 'shape': deepcopy(shape)}
                               for uid, shape in journal.owners.items() if shape['net'] in {A, B, C}}
        f.initial = deepcopy(f.state())
        calls, proposals = [], []
        missing = {T: 1, OPEN: 1}
        def assess(board, working, deadline):
            m._guard(deadline)
            if 'owner-old' in board.items:
                deficit = {T: 1, OPEN: 1}
            elif any(uid.startswith('candidate-') for uid in board.items):
                deficit = missing
            else:
                deficit = {T: 1, A: 1, B: 1, C: 1, OPEN: 1}
            return {'missing': deepcopy(deficit), 'partitions': {}, 'attached': set(board.items),
                    'frame': f.snapshot(board), 'graph': {}, 'coverage': set(journal.bank_nets)}
        def route(board, working, order, deadline):
            nonlocal missing
            order = tuple(order); calls.append(order)
            if mode == 'duplicate':
                if order[0] == A:
                    missing, dependencies = {T: 1, OPEN: 1}, {T: A}
                else:
                    missing = {A: 1, OPEN: 1}
                    dependencies = {} if order == REVERSE else {A: T}
            elif order in (SEED, REVERSE):
                # Both physical seeds have exact new-T witnesses. The second
                # offers unique alternatives while two promotions are pending.
                missing, dependencies = {A: 1, B: 1, OPEN: 1}, {A: T, B: T}
            elif order == FIRST:
                missing, dependencies = {B: 1, OPEN: 1}, {B: T}
            elif order == SECOND:
                # Same combined order as FIRST: it must not be queued twice.
                missing, dependencies = {A: 1, OPEN: 1}, {A: B}
            elif order == COMBINED:
                missing = {OPEN: 1} if mode == 'success' else {C: 1, OPEN: 1}
                dependencies = {} if mode in ('success', 'no_witness') else {C: B}
            elif order == FRONTIER:
                missing = {OPEN: 1} if mode == 'frontier_success' else {T: 1, OPEN: 1}
                dependencies = {} if mode == 'frontier_success' else {T: A}
            else:
                missing, dependencies = {A: 1, B: 1, OPEN: 1}, {A: T, B: T}
            events, emitted = [], {}
            for net in order:
                added = {}
                if net not in missing:
                    uid = 'candidate-%d-%s' % (len(calls), net)
                    before = f.snapshot(board)
                    board.Add(f.Track(uid, dict(template, net=net)))
                    added = m.record_feeder_route(board, working, before, [uid], net)
                    emitted[net] = (uid, added[uid])
                witnesses = {}
                if net in dependencies:
                    uid, shape = emitted[dependencies[net]]
                    witnesses[uid] = {'net': shape['net'], 'shape': deepcopy(shape)}
                    if order == COMBINED and mode == 'stale_witness':
                        witnesses[uid]['shape']['width'] += 1
                events.append({'net': net, 'connections': int(net not in missing),
                               'added': added, 'witnesses': witnesses, 'query_omissions': 0})
            # Record real helper output before the caller applies pending/dedup
            # caps; this does not manufacture or replace the helper's proposals.
            proposals.append((order, m._priority_orders(list(order), events, set(missing), journal.owners)))
            return events
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(m, '_assess', assess))
        stack.enter_context(patch.object(m, '_route_candidate', route))
        return f, calls, proposals

    def test_consumed_priority_slot_allows_two_successive_verified_promotions(self):
        f, calls, proposals = self.fixture('success')
        result = f.run()
        self.assertTrue(result.improved)
        self.assertEqual(calls, [SEED, REVERSE, FIRST, SECOND, COMBINED])
        self.assertEqual(proposals[2][1], [list(COMBINED)])
        self.assertEqual(result.before_missing, {T: 1, OPEN: 1})
        self.assertEqual(result.after_missing, {OPEN: 1})
        self.assertEqual(set(result.retired_ids), {'owner-old', 'b-old', 'c-old'})
        self.assertEqual(f.proofs, 1)

    def test_verified_priority_replaces_sixth_pending_ordinary_and_can_improve(self):
        f, calls, proposals = self.fixture('frontier_success')
        result = f.run()
        self.assertTrue(result.improved)
        self.assertEqual(calls, [SEED, REVERSE, FIRST, SECOND, COMBINED, FRONTIER])
        self.assertNotIn(ORDINARY, calls)
        self.assertEqual(proposals[4][1], [list(FRONTIER)])
        self.assertEqual(result.before_missing, {T: 1, OPEN: 1})
        self.assertEqual(result.after_missing, {OPEN: 1})
        self.assertEqual(set(result.retired_ids), {'owner-old', 'b-old', 'c-old'})
        self.assertEqual(f.proofs, 1)

    def test_no_valid_witness_keeps_the_pending_ordinary_order(self):
        for mode in ('no_witness', 'stale_witness'):
            with self.subTest(mode=mode):
                f, calls, proposals = self.fixture(mode)
                result = f.run()
                self.assertFalse(result.improved)
                self.assertEqual(calls, [SEED, REVERSE, FIRST, SECOND, COMBINED, ORDINARY])
                self.assertEqual(proposals[4][1], [])
                self.assertEqual(f.state(), f.initial)
                self.assertEqual(f.proofs, 0)

    def test_pending_two_and_six_executed_plus_pending_cap_survive_replacement(self):
        f, calls, proposals = self.fixture('cap')
        result = f.run()
        self.assertFalse(result.improved)
        self.assertEqual(calls[:2], [SEED, REVERSE])
        self.assertEqual(len(calls), 6)
        self.assertEqual(len(set(calls)), 6)
        self.assertIn(COMBINED, calls)
        # Unique second-seed proposals cannot displace the two pending orders.
        for option in proposals[1][1]: self.assertNotIn(tuple(option), calls)
        # The witnessed promotion occupies the sixth slot instead of the
        # pending ordinary. Further valid proposals cannot evict executed work.
        combined_proposals = next(options for order, options in proposals if order == COMBINED)
        self.assertEqual(combined_proposals, [[A, C, B, T]])
        self.assertEqual(calls[-1], FRONTIER)
        self.assertNotIn(ORDINARY, calls)
        self.assertTrue(proposals[-1][1])
        for option in proposals[-1][1]: self.assertNotIn(tuple(option), calls)
        self.assertEqual(f.state(), f.initial)
        self.assertEqual(f.proofs, 0)

    def test_consumed_orders_remain_deduplicated_when_pending_slots_are_free(self):
        f, calls, proposals = self.fixture('duplicate')
        result = f.run()
        self.assertFalse(result.improved)
        self.assertEqual(calls, [SEED, REVERSE, FIRST, (T, A, C, B), (A, T, C, B)])
        self.assertEqual(proposals[2][1], [list(SEED)])
        self.assertEqual(f.state(), f.initial)

    def test_changed_or_baseline_witness_cannot_create_a_priority_order(self):
        f, calls, proposals = self.fixture('success')
        shape = deepcopy(f.board.items['owner-old'].data)
        witness = {'net': A, 'shape': deepcopy(shape)}
        events = [{'net': A, 'added': {'new': shape}, 'witnesses': {}},
                  {'net': T, 'added': {}, 'witnesses': {'new': witness}}]
        self.assertEqual(f.module._priority_orders([A, T], events, {T}, {}), [[T, A]])
        self.assertEqual(f.module._priority_orders([A, T], events, {T}, {'new': shape}), [])
        witness['shape']['width'] += 1
        self.assertEqual(f.module._priority_orders([A, T], events, {T}, {}), [])
