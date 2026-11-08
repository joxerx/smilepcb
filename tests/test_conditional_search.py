import copy

import pytest
from smilepcb.keyboard_routing import conditional_search as search


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(search, 'monotonic', lambda: clock[0])
    return clock


def accepted(bank, option):
    return {'accepted': True, 'plan': {'bank': bank, 'id': option, 'shapes': {'owned': [1]}}}


def run(order, attempt, **kw):
    return search.conditional_search(order, attempt, deadline=10,
        leaf_validator=kw.pop('leaf_validator', lambda plans: {'accepted': True}), **kw)


def test_backtracks_complete_prefix_without_promoting_descendant_feedback():
    calls = []
    def attempt(bank, option, prefix, feedback, deadline):
        calls.append((bank, option, [p['id'] for p in prefix], feedback))
        if bank == 'second' and prefix[0]['id'] == 'original':
            return {'accepted': False, 'failed_interfaces': [('child-pad',)]}
        return accepted(bank, option)
    result = run(['first', 'second'], attempt)
    assert result['accepted'] is True
    assert [(p['bank'], p['id']) for p in result['selection']] == [('first', 'regenerated'), ('second', 'original')]
    assert calls[3] == ('first', 'regenerated', [], [])
    assert result['selection_attempts'] == [4, 5]
    assert result['attempt_records'][4]['prefix_attempts'] == [4]


def test_original_certified_feedback_is_canonical_and_parent_scoped():
    seen = []
    def attempt(bank, option, prefix, feedback, deadline):
        seen.append(feedback)
        if option == 'original':
            return {'accepted': False, 'reason': 'per-net timeout then completed native missing proof',
                    'failed_interfaces': [('z', 'a'), ('b',)]}
        return accepted(bank, option)
    result = run(['bank'], attempt)
    assert result['accepted'] is True
    assert seen == [[], [('a', 'z'), ('b',)]]


def test_truthy_rejected_leaf_backtracks_and_records_rejection():
    leaves = []
    def leaf(prefix):
        leaves.append(prefix[0]['id'])
        return {'accepted': prefix[0]['id'] == 'regenerated', 'reason': 'native union'}
    result = run(['bank'], lambda b, o, p, f, d: accepted(b, o), leaf_validator=leaf)
    assert result['accepted'] is True
    assert leaves == ['original', 'regenerated']
    assert result['leaf_rejections'][0]['prefix_attempts'] == [1]
    assert result['leaf_validations'] == 2


def test_attempt_cap_keeps_descendants_and_unexplored_ancestor_sibling():
    result = run(['a', 'b'], lambda b, o, p, f, d: accepted(b, o), max_attempts=1)
    assert not result['accepted'] and result['reason'] == 'attempt_limit'
    assert len(result['attempt_records']) == 1
    assert [(q['bank'], q['option'], q['prefix_attempts']) for q in result['frontier']] == [
        ('b', 'original', [1]), ('b', 'regenerated', [1]), ('a', 'regenerated', [])]
    assert result['frontier'][1]['feedback_status'] == 'pending_original'


def test_leaf_is_allowed_after_last_budgeted_attempt():
    result = run(['a'], lambda b, o, p, f, d: accepted(b, o), max_attempts=1)
    assert result['accepted'] is True and result['leaf_validations'] == 1


def test_global_deadline_after_attempt_preserves_leaf_frontier(fixed_clock):
    def attempt(bank, option, prefix, feedback, deadline):
        fixed_clock[0] = 11
        return accepted(bank, option)
    result = run(['a'], attempt)
    assert not result['accepted'] and result['reason'] == 'deadline'
    assert result['leaf_validations'] == 0
    assert result['frontier'][0]['option'] == 'validate_leaf'


def test_leaf_returned_success_after_deadline_is_not_admitted(fixed_clock):
    def leaf(prefix):
        fixed_clock[0] = 11
        return {'accepted': True}
    result = run(['a'], lambda b, o, p, f, d: accepted(b, o), leaf_validator=leaf)
    assert not result['accepted'] and result['reason'] == 'deadline'
    assert result['leaf_rejections'][0]['result']['reason'] == 'global_deadline_after_leaf'


def test_callback_timeout_is_recorded_without_invented_feedback():
    seen = []
    def attempt(bank, option, prefix, feedback, deadline):
        seen.append(feedback)
        if option == 'original':
            raise TimeoutError('inner native budget')
        return accepted(bank, option)
    result = run(['a'], attempt)
    assert result['accepted'] is True and seen == [[], []]
    assert result['attempt_records'][0]['result']['reason'] == 'attempt_timeout'


def test_global_timeout_after_final_rejection_is_still_deadline(fixed_clock):
    def attempt(bank, option, prefix, feedback, deadline):
        if option == 'regenerated':
            fixed_clock[0] = 11
            raise TimeoutError('global budget')
        return {'accepted': False}
    result = run(['a'], attempt)
    assert result['reason'] == 'deadline'


def test_expired_search_never_calls_adapter(fixed_clock):
    fixed_clock[0] = 11
    result = run(['a'], lambda *args: pytest.fail('adapter called after deadline'))
    assert result['reason'] == 'deadline'
    assert len(result['frontier']) == 2 and not result['attempt_records']


def test_feedback_callback_cannot_mutate_physical_certificate():
    def attempt(bank, option, prefix, feedback, deadline):
        if option == 'original':
            return {'accepted': False, 'failed_interfaces': [('pad',)]}
        feedback.clear()
        return accepted(bank, option)
    with pytest.raises(ValueError, match='mutated'):
        run(['a'], attempt)


def test_leaf_contract_requires_boolean_acceptance():
    with pytest.raises(ValueError):
        run(['a'], lambda b, o, p, f, d: accepted(b, o),
            leaf_validator=lambda prefix: {'accepted': 1})


def test_finite_rejection_is_not_a_physical_impossibility_claim():
    result = run(['a', 'b'], lambda *args: {'accepted': False, 'reason': 'no finite option'})
    assert result['reason'] == 'finite_search_exhausted'
    assert result['frontier'] == [] and len(result['attempt_records']) == 2
    assert result['physical_impossibility_proven'] is False


@pytest.mark.parametrize('result', [None, {}, {'accepted': 1}, {'accepted': True},
    {'accepted': True, 'plan': {'bank': 'wrong'}},
    {'accepted': True, 'plan': {'bank': 'a'}, 'failed_interfaces': [('pad',)]},
    {'accepted': False, 'failed_interfaces': [('x', 'y'), ('y', 'z')]}])
def test_malformed_callback_contract_is_not_geometric_rejection(result):
    with pytest.raises(ValueError):
        run(['a'], lambda *args: result)


def test_callback_cannot_mutate_retained_prefix():
    def attempt(bank, option, prefix, feedback, deadline):
        if prefix:
            prefix[0]['shapes']['owned'].append(9)
        return accepted(bank, option)
    with pytest.raises(ValueError, match='mutated'):
        run(['a', 'b'], attempt)


def test_leaf_cannot_mutate_selected_plans():
    def leaf(prefix):
        prefix[0]['shapes']['owned'].clear()
        return {'accepted': True}
    with pytest.raises(ValueError, match='mutated'):
        run(['a'], lambda b, o, p, f, d: accepted(b, o), leaf_validator=leaf)


def test_stored_plan_has_no_callback_alias():
    first = accepted('a', 'original')
    def attempt(bank, option, prefix, feedback, deadline):
        if bank == 'a':
            return first
        first['plan']['shapes']['owned'].append(99)
        assert prefix[0]['shapes']['owned'] == [1]
        return accepted(bank, option)
    result = run(['a', 'b'], attempt)
    assert result['selection'][0]['shapes']['owned'] == [1]


def cold_fixture():
    # Labels deliberately do not encode a direction or routing role.
    banks = {name: [{'pad_ids': [f'{stem}{i}']} for i in range(count)]
             for name, stem, count in [('alpha', 'n', 8), ('beta', 's', 6),
                                       ('gamma', 'e', 4), ('delta', 'w', 1)]}
    sides = {'alpha': [(1, -1)], 'beta': [(1, 1)],
             'gamma': [(0, 1)], 'delta': [(0, -1)]}
    return banks, sides


def test_cold_order_selects_loaded_adjacent_pair_not_loaded_opposites():
    banks, sides = cold_fixture()
    assert search.derive_cold_bank_order(banks, sides) == ['alpha', 'gamma', 'beta', 'delta']


def test_cold_order_is_label_and_input_order_independent():
    banks, sides = cold_fixture()
    before = copy.deepcopy((banks, sides))
    rename = {'alpha': 'last', 'beta': 'middle', 'gamma': 'first', 'delta': 'second'}
    other = {rename[b]: list(reversed(ports)) for b, ports in reversed(list(banks.items()))}
    side_other = {rename[b]: list(reversed(rows)) for b, rows in reversed(list(sides.items()))}
    assert search.derive_cold_bank_order(other, side_other) == ['last', 'first', 'middle', 'second']
    assert (banks, sides) == before


def test_cold_load_counts_interfaces_instead_of_alias_pads():
    banks = {'aliased': [{'pad_ids': ['a1', 'a2', 'a3']}],
             'loaded': [{'pad_ids': ['b1']}, {'pad_ids': ['b2']}]}
    sides = {'aliased': [(0, 1)], 'loaded': [(1, -1)]}
    assert search.derive_cold_bank_order(banks, sides) == ['loaded', 'aliased']


def test_alias_union_uses_every_actual_side_for_adjacency():
    banks = {'merged': [{'pad_ids': ['m1', 'm2']}],
             'left': [{'pad_ids': ['l1']}, {'pad_ids': ['l2']}],
             'right': [{'pad_ids': ['r1']}, {'pad_ids': ['r2']}]}
    sides = {'merged': [(1, -1), (1, 1)], 'left': [(0, -1)], 'right': [(0, 1)]}
    assert search.derive_cold_bank_order(banks, sides) == ['left', 'merged', 'right']


def test_opposite_only_and_single_bank_need_no_invented_adjacent_pair():
    banks, sides = cold_fixture()
    pair = {b: banks[b] for b in ('alpha', 'beta')}
    assert search.derive_cold_bank_order(pair, {b: sides[b] for b in pair}) == ['alpha', 'beta']
    assert search.derive_cold_bank_order({'beta': banks['beta']}, {'beta': sides['beta']}) == ['beta']


@pytest.mark.parametrize('mutation', ['missing_sides', 'duplicate_side', 'invalid_axis',
    'invalid_sign', 'boolean_axis', 'overlapping_pads', 'duplicate_alias', 'empty_pads',
    'empty_bank', 'empty_sides'])
def test_cold_order_rejects_incomplete_or_overlapping_physical_inventory(mutation):
    banks, sides = cold_fixture()
    if mutation == 'missing_sides': sides.pop('alpha')
    elif mutation == 'duplicate_side': sides['beta'] = sides['alpha'][:]
    elif mutation == 'invalid_axis': sides['beta'] = [(2, 1)]
    elif mutation == 'invalid_sign': sides['beta'] = [(1, 0)]
    elif mutation == 'boolean_axis': sides['beta'] = [(True, 1)]
    elif mutation == 'overlapping_pads': banks['beta'][0]['pad_ids'] = banks['alpha'][0]['pad_ids'][:]
    elif mutation == 'duplicate_alias': banks['beta'][0]['pad_ids'] = ['same', 'same']
    elif mutation == 'empty_pads': banks['beta'][0]['pad_ids'] = []
    elif mutation == 'empty_bank': banks['beta'] = []
    elif mutation == 'empty_sides': sides['beta'] = []
    with pytest.raises(ValueError): search.derive_cold_bank_order(banks, sides)
