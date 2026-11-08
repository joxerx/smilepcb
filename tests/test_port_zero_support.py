"""Check bounded port search without importing native CAD bindings."""
import ast
from itertools import product
from pathlib import Path
import random
import time

import pytest

from smilepcb.keyboard_routing.conditional_sources import select_head_set


def ordinary_selector():
    source = Path(__file__).parents[1] / 'smilepcb/keyboard_routing/controller_ports.py'
    tree = ast.parse(source.read_text())
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == '_select']
    assert len(nodes) == 1
    scope = {'time': time}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 str(source), 'exec'), scope)
    return scope['_select']


@pytest.fixture(params=['ordinary', 'conditional'])
def select(request):
    return ordinary_selector() if request.param == 'ordinary' else select_head_set


def test_zero_support_value_does_not_consume_solution_budget(select):
    domains = [[(0, a) for a in range(2)]] + [
        [(i, a) for a in range(3)] for i in range(1, 4)]

    def compatible(x, y):
        (i, a), (j, b) = sorted((x, y))
        # Value (0,0) wipes group 1 but scores loss 1. The viable (0,1)
        # scores loss 2, so the old search wastes a state on (0,0) first.
        return i != 0 or (j != 1 if a == 0 else b == 0)

    selected, stats = select(domains, compatible, time.monotonic()+5, 4)
    assert selected == {0: 1, 1: 0, 2: 0, 3: 0}
    assert stats['states'] == 4
    assert stats['zero_support_values'] == 1
    assert not stats.get('budget_exhausted', False)


def test_pruned_values_keep_prior_assignment_in_failure_causes(select):
    domains = [[(0, 0), (0, 1)], [(1, 0), (1, 1)],
               [(2, 0), (2, 1), (2, 2)]]

    def compatible(x, y):
        (i, a), (j, b) = sorted((x, y))
        if (i, j) == (0, 1):
            return True
        if (i, j) == (0, 2):
            return (a, b) in {(0, 0), (0, 1), (1, 2)}
        assert (i, j) == (1, 2)
        return b == 2

    # The first root value removes group 2's only support for group 1.
    # Its cause must survive pruning so backjumping tries the second root value.
    selected, stats = select(domains, compatible, time.monotonic()+5, 100)
    assert selected == {0: 1, 1: 0, 2: 2}
    assert stats['zero_support_values'] == 2
    assert not stats.get('budget_exhausted', False)


def test_final_incompatible_callback_cannot_hide_deadline_expiry(select, monkeypatch):
    class Clock:
        now = 0

        def monotonic(self):
            return self.now

    clock = Clock()
    monkeypatch.setitem(select.__globals__, 'time', clock)

    def incompatible(a, b):
        clock.now = 2
        return False

    selected, stats = select([[(0, 0)], [(1, 0)]], incompatible, 1, 4)
    assert selected is None
    assert stats.get('budget_exhausted', False)
    assert stats['states'] == 0


def test_state_cap_still_stops_a_nonterminal_assignment(select):
    selected, stats = select([[(0, 0)], [(1, 0)]], lambda a, b: True,
                             time.monotonic()+5, 1)
    assert selected is None
    assert stats['budget_exhausted']
    assert stats['states'] == 1


def binary_cases():
    rng = random.Random(1160104)
    for case in range(256):
        sizes = [rng.randrange(1, 4) for _ in range(rng.randrange(2, 6))]
        domains = [[(i, a) for a in range(size)] for i, size in enumerate(sizes)]
        density = rng.choice((.2, .5, .8))
        relation = {(i, a, j, b): rng.random() < density
                    for i in range(len(sizes)) for j in range(i+1, len(sizes))
                    for a in range(sizes[i]) for b in range(sizes[j])}

        def compatible(x, y, relation=relation):
            (i, a), (j, b) = sorted((x, y))
            return relation[i, a, j, b]

        # The oracle checks every complete tuple directly against the relation;
        # it shares no search, pruning, ordering or backjump implementation.
        expected = {values for values in product(*(range(size) for size in sizes))
                    if all(relation[i, values[i], j, values[j]]
                           for i in range(len(sizes)) for j in range(i+1, len(sizes)))}
        yield case, domains, compatible, expected


def test_small_binary_problems_match_cartesian_oracle(select):
    for case, domains, compatible, expected in binary_cases():
        selected, stats = select(domains, compatible, time.monotonic()+5, 10000)
        assert not stats.get('budget_exhausted', False), (case, stats)
        if expected:
            assert selected is not None, case
            assert tuple(selected[i] for i in range(len(domains))) in expected, case
        else:
            assert selected is None, case


def test_conditional_search_visits_every_legal_complete_tuple():
    for case, domains, compatible, expected in binary_cases():
        seen = set()

        def reject(rows):
            values = tuple(a for i, a in rows)
            assert values not in seen, (case, values)
            seen.add(values)
            return False

        selected, stats = select_head_set(domains, compatible, time.monotonic()+5,
                                          10000, accept_complete=reject)
        assert selected is None
        assert not stats.get('budget_exhausted', False), (case, stats)
        assert seen == expected, case
