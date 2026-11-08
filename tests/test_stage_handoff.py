import pytest
from smilepcb.keyboard_routing import handoff

@pytest.mark.parametrize('after,want',[
    ([['a','b'],['c']],True),([['a','b','c']],True),
    ([['a'],['b','c']],False),([['a','b']],False),
])
def test_merges_are_allowed_but_existing_contacts_cannot_split(after,want):
    assert handoff.preserves_contacts([['a','b'],['c']],after) is want

@pytest.mark.parametrize('groups',[[['a','b'],['b']], [[]]])
def test_ambiguous_or_empty_native_groups_fail_closed(groups):
    with pytest.raises(ValueError):handoff.preserves_contacts([['a','b']],groups)


def test_completed_nets_come_from_all_physical_contacts_not_names_or_counts():
    bindings={'a':'signal-z','alias':'signal-z','b':'supply','c':'supply','single':'testpoint'}
    assert handoff.completed_pad_nets([['a','alias'],['b'],['c'],['single']],bindings)=={'signal-z','testpoint'}

@pytest.mark.parametrize('groups,bindings',[
    ([['a']],{'a':'n','missing':'n'}),
    ([['a','b']],{'a':'n','b':'m'}),
    ([['a'],['a']],{'a':'n'}),
    ([['a']],{'a':''}),
])
def test_incomplete_or_ambiguous_topology_cannot_mark_a_net_complete(groups,bindings):
    with pytest.raises(ValueError):handoff.completed_pad_nets(groups,bindings)
