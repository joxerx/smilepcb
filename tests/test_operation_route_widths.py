"""Physical via diameters survive operation-log route reconstruction."""
import ast
from copy import deepcopy
from pathlib import Path

import pytest

from smilepcb.keyboard_routing.layered_simple_route import safe_layered_route


def parser():
    root = Path(__file__).resolve().parents[1] / 'smilepcb/keyboard_routing'
    namespace = {}
    for file, name in [('collector_capacity.py', 'clean'), ('obstacle_bundles.py', '_route_from_operations')]:
        source = root / file
        node = next(node for node in ast.parse(source.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['_route_from_operations']


def track(face, path):
    return dict(kind='track', layer=face, path=path, width=150000, net='probe')


def operations():
    return [track(0, [(0, 0), (1000000, 0)]),
            track(2, [(1000000, 0), (2000000, 0)]),
            track(0, [(2000000, 0), (3000000, 0)]),
            dict(kind='via', position=(2000000, 0), width=500000),
            dict(kind='via', position=(1000000, 0), width=3200000)]


def test_mixed_diameters_follow_route_transition_order_not_operation_log_order():
    ops = operations(); before = deepcopy(ops)
    route = parser()(ops)
    assert route['vias'] == [(1000000, 0), (2000000, 0)]
    assert route['via_widths'] == [3200000, 500000]
    assert ops == before


@pytest.mark.parametrize('width', [None, 0, -1, '500000'])
def test_unresolved_or_invalid_transition_diameter_rejects(width):
    ops = operations()
    ops[-1]['width'] = width
    assert parser()(ops) is None


def test_omitted_portal_width_requires_explicit_current_world_default():
    ops = operations()
    del ops[-2]['width']
    assert parser()(ops) is None
    route = parser()(ops, default_via_width=600000)
    assert route['via_widths'] == [3200000, 600000]


def test_fixed_endpoint_via_is_not_a_leg_transition_or_an_invented_width():
    ops = operations()
    ops += [dict(kind='via', position=(0, 0), fixed=True),
            dict(kind='via', position=(3000000, 0), width=800000, fixed=True)]
    route = parser()(ops)
    assert route['vias'] == [(1000000, 0), (2000000, 0)]
    assert route['via_widths'] == [3200000, 500000]
    ops[-1]['fixed'] = False
    assert parser()(ops) is None


def test_small_via_late_reentry_stays_rejected_after_operation_reconstruction():
    m = 1000000
    tail = [(0, 0), (500000, 0), (m, 500000), (m, m), (500000, 1500000),
            (0, 1500000), (-500000, m), (-500000, 500000), (-200000, 200000)]
    ops = [track(0, [(-21*m, 0), (-20*m, 0)]), track(2, [(-20*m, 0), (0, 0)]),
           track(0, tail), dict(kind='via', position=(0, 0), width=500000),
           dict(kind='via', position=(-20*m, 0), width=3200000)]
    route = parser()(ops)
    assert route['via_widths'] == [3200000, 500000]
    assert not safe_layered_route(route, 150000, 3200000)
    legacy = {key: value for key, value in route.items() if key != 'via_widths'}
    assert safe_layered_route(legacy, 150000, 3200000)
