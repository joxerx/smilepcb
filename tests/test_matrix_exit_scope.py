"""A generated matrix can rebind GPIO roles only through an explicit total map."""
from copy import deepcopy
import pytest


def validate(mapping, families, source_nets, scope='within-role'):
    from smilepcb.controller_gpio import validate_exit_mapping
    return validate_exit_mapping(mapping, families, source_nets, assignment_scope=scope)


def test_explicit_matrix_scope_allows_cross_role_bijection_without_mutating_input():
    mapping = {'row-signal': 'col-signal', 'col-signal': 'row-signal'}
    before = deepcopy(mapping)
    families = {'row-signal': 'rows', 'col-signal': 'columns'}
    assert validate(mapping, families, list(mapping), 'matrix') == before
    assert mapping == before


def test_default_scope_still_rejects_cross_role_mapping():
    with pytest.raises(ValueError, match='within-role'):
        validate({'r': 'c', 'c': 'r'}, {'r': 'rows', 'c': 'columns'}, ['r', 'c'])


def test_matrix_scope_requires_every_logical_matrix_function():
    with pytest.raises(ValueError, match='every matrix'):
        validate({'r': 'r'}, {'r': 'rows', 'c': 'columns'}, ['r'], 'matrix')


@pytest.mark.parametrize('mapping,sources', [
    ({'r': 'fixed-usb', 'c': 'r'}, ['r', 'c']),
    ({'r': 'c', 'c': 'c'}, ['r', 'c']),
    ({'r': 'c', 'c': 'r'}, ['r', 'r', 'c']),
])
def test_fixed_function_nonbijective_and_duplicate_source_maps_are_rejected(mapping, sources):
    with pytest.raises(ValueError):
        validate(mapping, {'r': 'rows', 'c': 'columns'}, sources, 'matrix')


def test_unknown_scope_is_rejected_instead_of_relaxing_a_guard():
    with pytest.raises(ValueError, match='scope'):
        validate({'r': 'r'}, {'r': 'rows'}, ['r'], None)
