"""Geometry coverage of a bounded visibility proposal set, without KiCad."""
import math

import pytest

from smilepcb.keyboard_routing.joint_repair import search_paths
from smilepcb.keyboard_routing.joint_visibility import visibility_points


def _segment_distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    scale = dx * dx + dy * dy
    t = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / scale)) if scale else 0
    return math.dist(point, (a[0] + t * dx, a[1] + t * dy))


@pytest.mark.parametrize('offset', [(0, 0), (1237, -8491)])
def test_interior_layer_change_remains_reachable_under_endpoint_projection_pressure(offset):
    """Endpoint hints must not remove the two interior via pockets."""
    dx, dy = offset
    shift = lambda q: (q[0] + dx, q[1] + dy)
    starts = [(shift((-10000000, 0)), 0)]
    goals = [(shift((10000000, 0)), 0)]
    contacts = [shift((x, y)) for x in (-10000000, 10000000)
                for y in (-50000, -30000, -10000, 0, 10000, 30000, 50000)]
    circles = [dict(center=shift((x, 1000000)), radius=100000, clearance=100000)
               for x in (-1500000, 1500000)]
    obstacles = circles + [dict(bounds=(x + dx + i * 113 - 100000, y + dy + i * 197 - 100000,
                                        x + dx + i * 113 + 100000, y + dy + i * 197 + 100000), clearance=100000)
                           for i, (x, y) in enumerate((x, y)
                               for x in range(-8000000, 8000001, 1000000)
                               for y in range(-8000000, -1999999, 1000000))]
    points = visibility_points(contacts, obstacles,
        bounds=(-12000000 + dx, -10000000 + dy, 12000000 + dx, 3000000 + dy),
        width=150000, via_width=500000, max_points=768)
    assert len(points) <= 768 and set(contacts) <= set(points)

    # Physical routing regions: two disconnected front regions, bridged only
    # by a back region whose two overlap pockets require interior via sites.
    regions = {0: [(-11000000, -1000000, -1000000, 2000000),
                   (1000000, -1000000, 11000000, 2000000)],
               2: [(-2000000, 1100000, 2000000, 2000000)]}
    def inside(point, box):
        x, y = point[0] - dx, point[1] - dy
        return box[0] <= x <= box[2] and box[1] <= y <= box[3]
    def edge(a, b, face):
        if not any(inside(a, box) and inside(b, box) for box in regions[face]):
            return None
        if any(_segment_distance(q['center'], a, b) <= 275000 for q in circles):
            return None
        return {'path': [a, b], 'cost': math.dist(a, b), 'blockers': set()}
    def via(point):
        if not all(any(inside(point, box) for box in regions[face]) for face in (0, 2)):
            return None
        if any(math.dist(point, q['center']) <= 450000 for q in circles):
            return None
        return set()
    plans = search_paths(points, starts, goals, edge, via, soft_ids=set(),
        deadline=float('inf'), max_results=1, max_vias=2, max_states=4000)
    assert plans, 'A bounded portal set lost the only two-layer corridor'
    assert len(plans[0]['vias']) == 2
    assert all(via(q) == set() for q in plans[0]['vias'])


def test_mandatory_terminal_overflow_is_explicit_instead_of_silent_loss():
    with pytest.raises(ValueError, match='mandatory'):
        visibility_points([(0, 0), (1, 0), (2, 0)], [],
            bounds=(-1, -1, 3, 1), width=1, via_width=2, max_points=2)


@pytest.mark.parametrize('offset', [(0, 0), (1237, -8491)])
def test_exact_narrow_medial_keeps_its_slot_when_the_cap_is_full(offset):
    dx, dy = offset
    circles = [{'center': (dx, dy), 'radius': 250000, 'clearance': 174000},
               {'center': (dx, dy + 1000000), 'radius': 250000, 'clearance': 174000}]
    contacts = [(dx - 900000, dy + 500000), (dx + 900000, dy + 500000)]
    diagnostic = {}
    points = visibility_points(contacts, circles,
        bounds=(dx - 2000000, dy - 1000000, dx + 2000000, dy + 2000000),
        width=150000, via_width=500000, max_points=3, diagnostic=diagnostic)
    assert set(points) == {*contacts, (dx, dy + 500000)}
    assert diagnostic['selected_points_nm'] == [list(q) for q in points]
    assert diagnostic['mandatory_points'] == 3
    assert diagnostic['local_selected'] == diagnostic['projection_selected'] == 0
