"""One physical source allowance survives full and single-interface searches."""
import pytest

from smilepcb.keyboard_routing import conditional_sources as sources
from test_port_search_portfolio import chooser


def budget(groups=(('a', 'alias'), ('b',)), candidates=96, states=12000):
    return sources.SourceSearchBudget(groups, max_candidates=candidates, max_states=states)


def test_alias_order_has_one_allowance_and_partial_alias_cannot_get_a_new_one():
    shared = budget()
    shared.charge_candidates(('alias', 'a'), 95)
    assert shared.candidate_limit(('a', 'alias'), 96) == 1
    shared.charge_candidates(('a', 'alias'), 1)
    assert shared.candidate_limit(('alias', 'a'), 96) == 0
    assert shared.candidate_limit(('b',), 96) == 96
    with pytest.raises(ValueError):
        shared.candidate_limit(('a',), 96)
    with pytest.raises(ValueError):
        shared.charge_candidates(('a', 'alias'), 1)


@pytest.mark.parametrize('preferred_success', [True, False])
def test_actual_selector_calls_charge_preferred_once(preferred_success):
    shared = budget(states=10)
    calls = []
    def select(ds, compatible, end, maximum):
        calls.append(maximum)
        return ({0: 0} if preferred_success or len(calls) == 2 else None), {'states': 2}
    domains = [[{'kind': 'same_layer'}, {'kind': 'via'}]]
    result, _, stats = chooser()(domains, lambda a,b: True, 1e30, 10, True,
                                shared.selector(select))
    assert result is not None
    assert calls == ([5] if preferred_success else [5, 8])
    assert shared.states_left == (8 if preferred_success else 6)
    assert stats['preferred_bank_search']['states'] == 2


def test_identical_domains_and_later_target_share_remaining_states():
    shared = budget(states=5)
    calls = []
    def select(ds, compatible, end, maximum):
        calls.append(maximum)
        return None, {'states': maximum}
    chooser()([[{'kind': 'same_layer'}]], lambda a,b: True, 1e30, 5, True,
              shared.selector(select))
    assert shared.states_left == 0 and calls == [5]
    result, stats = shared.selector(select)([], lambda a,b: True, 1e30, 5)
    assert result is None and stats['budget_exhausted'] and calls == [5]


def test_partial_domains_charge_immediately_and_receipt_is_serializable():
    import json
    shared = budget()
    shared.charge_candidates(('a', 'alias'), 20)
    shared.charge_candidates(('b',), 0)
    receipt = json.loads(json.dumps(shared.receipt()))
    assert receipt['candidate_domains'] == [
        {'pad_ids': ['a', 'alias'], 'spent': 20, 'remaining': 76},
        {'pad_ids': ['b'], 'spent': 0, 'remaining': 96}]
    assert receipt['states_spent'] == 0
