"""Via contact belongs to an endpoint interval, not just one track segment."""
from copy import deepcopy

import pytest

from smilepcb.keyboard_routing.bundle_geometry import paths_clear
from smilepcb.keyboard_routing.layered_simple_route import safe_layered_route
from smilepcb.keyboard_routing.polish_geometry import valid_shape


M = 1000000
ORIGIN = (0, 0)
FRONT = [(-M, 0), ORIGIN]
CASES = {
    'short-neck': (True, [ORIGIN, (0, 100000), (500000, 600000)]),
    'long-neck': (True, [ORIGIN, (0, 400000), (500000, 900000)]),
    'several-bends-inside-cap': (True, [ORIGIN, (0, 100000), (100000, 200000),
                                     (200000, 200000), (600000, -200000)]),
    'late-reentry': (False, [ORIGIN, (M, 0), (2*M, M), (2*M, 2*M), (M, 3*M),
                            (0, 3*M), (-M, 2*M), (-M, M), (-200000, 200000)]),
    'chord-reentry': (False, [ORIGIN, (M, 0), (2*M, M), (2*M, 2*M), (M, 3*M),
                             (0, 3*M), (-M, 2*M), (-M, 700000), (500000, -800000)]),
}


@pytest.mark.parametrize('scale', [1, 3])
@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('case', CASES)
def test_incident_contact_is_a_single_contiguous_endpoint_interval(case, reverse, scale):
    expected, back = CASES[case]
    route = {'legs': [(0, FRONT), (2, back)], 'vias': [ORIGIN]}
    if reverse:
        route['legs'] = [(face, list(reversed(path))) for face, path in reversed(route['legs'])]
    route['legs'] = [(face, [(scale*x, scale*y) for x, y in path]) for face, path in route['legs']]
    assert all(valid_shape(path) and paths_clear([path], [150000*scale], 0) for _, path in route['legs'])
    before = deepcopy(route)
    assert safe_layered_route(route, 150000*scale, 500000*scale) is expected
    assert route == before


def test_nonincident_leg_cannot_touch_via_even_when_every_path_is_clear():
    route = {'legs': [(0, FRONT),
                      (2, [ORIGIN, (M, 0), (2*M, M), (2*M, 2*M)]),
                      (0, [(2*M, 2*M), (M, 3*M), (0, 3*M), (-M, 2*M),
                           (-M, M), (-200000, 200000)])],
             'vias': [ORIGIN, (2*M, 2*M)]}
    assert paths_clear([route['legs'][0][1], route['legs'][2][1]], [150000, 150000], 0)
    assert all(valid_shape(path) for _, path in route['legs'])
    assert not safe_layered_route(route, 150000, 500000)


def mixed_via_route():
    return {'legs': [(0, [(-21*M, 0), (-20*M, 0)]), (2, [(-20*M, 0), ORIGIN]),
                     (0, [ORIGIN, (500000, 0), (M, 500000), (M, M), (500000, 1500000),
                          (0, 1500000), (-500000, M), (-500000, 500000), (-200000, 200000)])],
            'vias': [(-20*M, 0), ORIGIN]}


def test_large_previous_via_does_not_enlarge_small_vias_endpoint_contact_cap():
    route = mixed_via_route()
    assert safe_layered_route(route, 150000, 3200000)
    route['via_widths'] = [3200000, 500000]
    assert not safe_layered_route(route, 150000, 3200000)


@pytest.mark.parametrize('widths', [[], [500000], [3200000, 0], [3200000, None],
                                  [3200000, float('nan')], [3200000, float('inf')]])
def test_present_per_via_width_metadata_is_validated_fail_closed(widths):
    route = mixed_via_route()
    route['via_widths'] = widths
    assert not safe_layered_route(route, 150000, 3200000)
