"""Opposite collector turns must depart from their respective rail ends."""
from copy import deepcopy

import pytest

from smilepcb.keyboard_routing.collector_assignment import collector_assignment


# Six actual pre-assignment heads from the ANSI104 fresh-source receipt.
# Field centroids are native non-MCU pad centroids; units are nanometres.
def observed_inputs():
    rows = [
        ('COL8', 236125000, 73462500),
        ('COL9', 236625000, 76712500),
        ('COL11', 237125000, 72837500),
        ('COL15', 237875000, 73212500),
        ('COL13', 239750000, 74087500),
        ('COL10', 240125000, 74462500),
    ]
    ports = [dict(net=name, bank=['chip', 1, -1], exit_layer=0,
                  exit_nm=[x, 71837500], heading_origin_nm=[x, y])
             for name, x, y in rows]
    targets = {
        'COL8': (160133333.33333334, 45085000),
        'COL9': (179183333.33333334, 45085000),
        'COL10': (199582708.33333334, 54610000),
        'COL11': (219426458.33333334, 54610000),
        'COL13': (263082708.33333334, 54610000),
        'COL15': (313485833.3333333, 52705000),
    }
    return ports, targets, {name: 'columns' for name in targets}


OBSERVED_EXPECTED = {
    'COL8': 'COL8', 'COL9': 'COL9', 'COL11': 'COL10',
    'COL15': 'COL11', 'COL13': 'COL13', 'COL10': 'COL15',
}


def test_observed_north_rail_does_not_send_opposite_collectors_across_each_other():
    ports, targets, families = observed_inputs()
    before = deepcopy((ports, targets, families))
    mapping, plans = collector_assignment(ports, targets, families)
    assert mapping == OBSERVED_EXPECTED
    assert [mapping[q['net']] for q in ports] == [
        'COL8', 'COL9', 'COL10', 'COL11', 'COL13', 'COL15']
    assert {tuple(q['collector_heading']): q['signal_peel_order'] for q in plans} == {
        (-1, 0): ['COL11', 'COL10', 'COL9', 'COL8'],
        (1, 0): ['COL13', 'COL15'],
    }
    assert (ports, targets, families) == before


ORTHOGONAL_TRANSFORMS = [
    (1, 0, 0, 1), (-1, 0, 0, 1), (1, 0, 0, -1), (-1, 0, 0, -1),
    (0, 1, 1, 0), (0, -1, 1, 0), (0, 1, -1, 0), (0, -1, -1, 0),
]


@pytest.mark.parametrize('matrix', ORTHOGONAL_TRANSFORMS)
@pytest.mark.parametrize('translation', [(0, 0), (-523456789, 765432101)])
def test_direction_chunks_follow_geometry_under_renaming_and_rigid_transforms(matrix, translation):
    ports, targets, families = observed_inputs()
    names = {'COL8': 'zeta', 'COL9': 'beta', 'COL10': 'omega',
             'COL11': 'alpha', 'COL13': 'gamma', 'COL15': 'eta'}
    a, b, c, d = matrix
    def point(q):
        return [a*q[0]+b*q[1]+translation[0], c*q[0]+d*q[1]+translation[1]]
    for q in ports:
        q['net'] = names[q['net']]
        q['exit_nm'] = point(q['exit_nm'])
        q['heading_origin_nm'] = point(q['heading_origin_nm'])
    targets = {names[n]: point(q) for n, q in targets.items()}
    families = {names[n]: 'rows' if b else 'columns' for n in families}
    mapping, _ = collector_assignment(list(reversed(ports)), targets, families)
    assert mapping == {names[n]: names[v] for n, v in OBSERVED_EXPECTED.items()}
    assert set(mapping) == set(mapping.values()) == set(targets)


@pytest.mark.parametrize('isolation', ['rail', 'bank', 'face', 'family'])
def test_assignment_preserves_separate_physical_groups_and_matrix_roles(isolation):
    ports, targets, families = observed_inputs()
    isolated = deepcopy(ports[2])
    isolated['net'] = 'separate'
    if isolation == 'rail':
        # Actual COL12 rail is only 125 micrometres ahead of the common rail.
        isolated.update(exit_nm=[238625000, 71712500],
                        heading_origin_nm=[238625000, 72212500])
    elif isolation == 'bank':
        isolated['bank'] = ['other-chip', 1, -1]
    elif isolation == 'face':
        isolated['exit_layer'] = 2
    targets['separate'] = (239667083.33333334, 48895000)
    families['separate'] = 'rows' if isolation == 'family' else 'columns'
    mapping, plans = collector_assignment([isolated, *ports], targets, families)
    assert mapping == {**OBSERVED_EXPECTED, 'separate': 'separate'}
    assert set(mapping) == set(mapping.values()) == set(targets)
    assert all(families[n] == families[v] for n, v in mapping.items())
    if isolation != 'family':
        assert [q['signal_peel_order'] for q in plans
                if 'separate' in q['signal_peel_order']] == [['separate']]


@pytest.mark.parametrize('source_sign', [-1, 1])
@pytest.mark.parametrize('target_sign', [-1, 1])
def test_one_direction_keeps_existing_exposed_peel_order(source_sign, target_sign):
    ports = [dict(net=n, bank=['chip', 1, source_sign], exit_layer=0,
                  exit_nm=[x, 0], heading_origin_nm=[x, -source_sign*1000000])
             for n, x in [('a', -1000000), ('b', 0), ('c', 1000000)]]
    targets = {'a': (target_sign*5000000, -10000000),
               'b': (target_sign*15000000, -10000000),
               'c': (target_sign*25000000, -10000000)}
    mapping, plans = collector_assignment(ports, targets, {n: 'columns' for n in targets})
    expected = {'a': 'a', 'b': 'b', 'c': 'c'} if source_sign != target_sign else {
        'a': 'c', 'b': 'b', 'c': 'a'}
    assert mapping == expected
    assert plans[0]['signal_peel_order'] == ['a', 'b', 'c']


def test_opposite_parallel_collectors_keep_existing_mapping():
    ports = [dict(net=n, bank=['chip', 0, 1], exit_layer=0,
                  exit_nm=[0, y], heading_origin_nm=[-1000000, y])
             for n, y in [('a', -1000000), ('b', 0), ('c', 1000000)]]
    targets = {'a': (-5000000, -10000000), 'b': (15000000, -10000000),
               'c': (25000000, -10000000)}
    mapping, plans = collector_assignment(ports, targets, {n: 'columns' for n in targets})
    assert mapping == {'a': 'b', 'b': 'c', 'c': 'a'}
    assert {tuple(q['collector_heading']): q['signal_peel_order'] for q in plans} == {
        (-1, 0): ['a'], (1, 0): ['b', 'c']}
