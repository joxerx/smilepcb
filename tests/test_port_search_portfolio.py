"""Exercise the production phase allocator without importing pcbnew."""
import ast
from pathlib import Path
import time

from smilepcb.keyboard_routing.conditional_sources import select_head_set


def chooser():
    tree = ast.parse((Path(__file__).parents[1] / 'smilepcb/keyboard_routing/controller_ports.py').read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_select_access_domains']
    assert len(nodes) == 1, 'Production access-domain allocator is absent'
    scope = {'time': time}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), '<production allocator>', 'exec'), scope)
    return scope['_select_access_domains']


def test_identical_preferred_domains_use_full_budget_and_reach_later_solution():
    domains = [[{'kind': 'same_layer', 'value': i} for i in range(8)]]
    calls = []
    stop = time.monotonic() + 2
    def select(ds, compatible, deadline, max_states):
        calls.append((ds, deadline, max_states))
        return select_head_set(ds, compatible, deadline, max_states,
                               accept_complete=lambda rows: rows[0]['value'] == 5)
    # Two independent half-budget searches both miss the sixth legal leaf.
    assert select_head_set(domains, lambda a, b: True, stop, 4,
                           accept_complete=lambda rows: rows[0]['value'] == 5)[0] is None
    selected, used, stats = chooser()(domains, lambda a, b: True, stop, 8, True, select)
    assert selected == {0: 5} and used is domains
    assert len(calls) == 1 and calls[0][1:] == (stop, 8)
    assert stats['states'] == 6 and stats['preferred_bank_search_skipped'] == 'identical_domains'


def test_all_via_domains_do_not_repeat_the_fallback_as_preferred():
    domains = [[{'kind': 'via'}], [{'kind': 'same_layer'}]]
    calls = []
    def select(ds, compatible, deadline, max_states):
        calls.append((ds, max_states)); return {0: 0, 1: 0}, {'states': 2}
    selected, used, stats = chooser()(domains, lambda a, b: True, time.monotonic()+2, 10, True, select)
    assert len(calls) == 1 and calls[0][1] == 10
    assert used is domains and selected == {0: 0, 1: 0}


def test_reduced_preferred_success_returns_its_actual_domain_indices():
    via, same = {'kind': 'via'}, {'kind': 'same_layer'}
    domains = [[via, same]]; calls = []
    def select(ds, compatible, deadline, max_states):
        calls.append(max_states); assert ds[0][0] is same
        return {0: 0}, {'states': 1}
    selected, used, stats = chooser()(domains, lambda a, b: True, time.monotonic()+2, 10, True, select)
    assert calls == [5] and used[0][selected[0]] is same
    assert domains[0] == [via, same]
    assert stats['preferred_bank_search'] == {'states': 1}
    assert stats['preferred_bank_search'] is not stats


def test_reduced_preferred_failure_debits_actual_states_and_keeps_original_deadline():
    domains = [[{'kind': 'via'}, {'kind': 'same_layer'}]]; calls = []
    stop = time.monotonic()+2
    def select(ds, compatible, deadline, max_states):
        calls.append((ds, deadline, max_states))
        return (None, {'states': 2}) if len(calls) == 1 else ({0: 0}, {'states': 3})
    selected, used, stats = chooser()(domains, lambda a, b: True, stop, 10, True, select)
    assert [call[2] for call in calls] == [5, 8]
    assert calls[0][1] <= stop and calls[1][1] == stop
    assert selected == {0: 0} and used is domains
    assert stats['preferred_bank_search'] == {'states': 2}


def test_no_preference_keeps_single_full_search():
    domains = [[{'kind': 'via'}, {'kind': 'same_layer'}]]; calls = []
    def select(ds, compatible, deadline, max_states):
        calls.append(max_states); return None, {'states': max_states}
    selected, used, stats = chooser()(domains, lambda a, b: True, time.monotonic()+2, 9, False, select)
    assert calls == [9] and used is domains and selected is None
    assert 'preferred_bank_search' not in stats and 'preferred_bank_search_skipped' not in stats
