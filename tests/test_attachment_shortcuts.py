"""Attachment-only shortcut ownership and whole-route reconstruction."""
from copy import deepcopy

import pytest

from smilepcb.keyboard_routing import attachment_shortcuts as shortcuts
from smilepcb.keyboard_routing.layered_simple_route import safe_layered_route


def track(net, face, a, z, **extra):
    return dict(kind='track', net=net, layer=face, start=a, end=z, width=150000, **extra)


def plan():
    ops = []
    for net, y in [('lane-a', -200000), ('lane-b', 200000)]:
        ops.append(track(net, 0, (-1000000, y), (0, y), fixed=True))
        ops.append(track(net, 0, (3000000, y), (4000000, y), fixed=True))
        ops.append(track(net, 0, (1000000, y), (3000000, y)))
        path = [(0, y), (100000, y), (300000, y+200000), (700000, y+200000),
                (900000, y), (1000000, y)]
        ops.extend(track(net, 0, a, z) for a, z in zip(path, path[1:]))
    return dict(planned=True, nets=['lane-a', 'lane-b'], pitch_nm=400000,
                axis=[dict(kind='track', layer=0, end_layer=0, start=(1000000, 0), end=(3000000, 0))],
                operations=ops, removed_ids=['old-a', 'old-b'], deferred_zone_ids=[])


def test_scope_freezes_exact_fixed_prefix_suffix_and_reconstructed_shared_body():
    original = plan(); before = deepcopy(original)
    scope = shortcuts.prepare(original)
    assert scope['body_indices'] == [2, 10]
    assert scope['frozen_indices'] == [0, 1, 2, 8, 9, 10]
    assert len(scope['spans']) == 2
    assert all(len(row['ids']) == 5 for row in scope['spans'])
    assert original == before


def test_shortcut_replacement_keeps_frozen_operations_and_span_endpoints_exact():
    original = plan(); scope = shortcuts.prepare(original); span = scope['spans'][0]
    path = [span['path'][0], span['path'][-1]]
    changed = shortcuts.replace(original['operations'], {tuple(span['ids']): (span, path)})
    assert all(original['operations'][i] in changed for i in scope['frozen_indices'])
    assert len(changed) == len(original['operations']) - 4
    assert shortcuts.ordered_route(changed, span['net']) == {
        'legs': [(0, [(-1000000, -200000), (4000000, -200000)])], 'vias': [], 'via_widths': []}


def test_ambiguous_or_missing_body_geometry_is_not_editable():
    for change in ('missing', 'duplicate'):
        original = plan()
        if change == 'missing': original['operations'][2]['end'] = (2999999, -200000)
        else: original['operations'].append(deepcopy(original['operations'][2]))
        with pytest.raises(ValueError): shortcuts.prepare(original)


def test_unordered_emission_log_reconstructs_actual_via_order_and_diameters():
    ops = [dict(kind='via', net='n', position=(20000000, 0), width=500000, drill=200000),
           track('n', 0, (20000000, 0), (21000000, 0)),
           track('n', 0, (0, 0), (1000000, 0)),
           dict(kind='via', net='n', position=(1000000, 0), width=3200000, drill=200000),
           track('n', 2, (1000000, 0), (20000000, 0))]
    route = shortcuts.ordered_route(ops, 'n')
    assert route['vias'] == [(1000000, 0), (20000000, 0)]
    assert route['via_widths'] == [3200000, 500000]
    assert safe_layered_route(route, 150000, 500000)


def test_branch_or_disconnected_operation_union_fails_closed():
    ops = [track('n', 0, (0, 0), (1000000, 0)), track('n', 0, (1000000, 0), (2000000, 0))]
    assert shortcuts.ordered_route(ops+[track('n', 0, (1000000, 0), (1000000, 1000000))], 'n') is None
    assert shortcuts.ordered_route(ops+[track('n', 0, (4000000, 0), (5000000, 0))], 'n') is None
