"""Pure geometry contracts for finite-width corridor axis search."""
import importlib
import math
import time

import pytest


def search(**overrides):
    try:
        solve = importlib.import_module('smilepcb.keyboard_routing.obstacle_axis').search_corridor
    except ModuleNotFoundError:
        pytest.fail('The pure obstacle-aware corridor search is not implemented')
    args = dict(start=(0, 0), goal=(12, 0), start_heading=(1, 0),
                layers=(0, 2), preferred_layer=0, bounds_nm=(-4, -10, 16, 10),
                step_nm=1, minimum_run_nm=2, edge_clear=lambda a, z, layer: True,
                portal=None, deadline=time.monotonic() + 5, start_layers=(0,),
                goal_layers=(0,), bend_cost_nm=2)
    args.update(overrides)
    start, goal = args.pop('start'), args.pop('goal')
    return solve(start, goal, **args)


def hits_box(a, z, box):
    """Closed segment/rectangle intersection, including boundary contacts."""
    lo, hi = 0., 1.
    for p, q, lower, upper in zip(a, z, box[:2], box[2:]):
        delta = q - p
        if not delta:
            if not lower <= p <= upper:
                return False
        else:
            t0, t1 = sorted(((lower - p) / delta, (upper - p) / delta))
            lo, hi = max(lo, t0), min(hi, t1)
            if lo > hi:
                return False
    return True


def test_rectangle_on_both_faces_requires_xy_detour():
    box = (6, -3, 8, 3)
    result = search(edge_clear=lambda a, z, layer: not hits_box(a, z, box))
    assert result['accepted'], result
    assert any(abs(e['end'][1]) > 3 for e in result['edges'])
    assert all(not hits_box(e['start'], e['end'], box) for e in result['edges'])
    assert result['edges'][0]['start'] == (0, 0)
    assert result['edges'][-1]['end'] == (12, 0)


def test_wall_on_one_face_is_bridged_with_legal_portals():
    payload = object()
    def portal(point, heading, layer):
        if heading == (1, 0) and (point, layer) in (((2, 0), 0), ((8, 0), 2)):
            return dict(end=(point[0] + 1, 0), end_layer=2 - layer,
                        cost_nm=3, payload=payload)
    result = search(bounds_nm=(0, 0, 12, 0), portal=portal,
                    edge_clear=lambda a, z, layer: layer == 2 or not hits_box(a, z, (4, -1, 6, 1)))
    assert result['accepted'], result
    portals = [e for e in result['edges'] if e['kind'] == 'portal']
    assert [(e['layer'], e['end_layer']) for e in portals] == [(0, 2), (2, 0)]
    assert all(e['payload'] is payload for e in portals)


def test_bends_are_45_degrees_with_sufficient_incoming_and_outgoing_run():
    result = search(edge_clear=lambda a, z, layer: not hits_box(a, z, (6, -3, 8, 3)))
    assert result['accepted'], result
    runs = []
    for edge in result['edges']:
        dx, dy = (edge['end'][i] - edge['start'][i] for i in (0, 1))
        heading = (int(math.copysign(1, dx)) if dx else 0,
                   int(math.copysign(1, dy)) if dy else 0)
        length = math.hypot(dx, dy)
        if runs and heading == runs[-1][0]:
            runs[-1] = (heading, runs[-1][1] + length)
        else:
            runs.append((heading, length))
    assert len(runs) > 2
    assert all(length >= 2 for _, length in runs)
    for (a, _), (b, _) in zip(runs, runs[1:]):
        assert math.isclose(sum(x*y for x, y in zip(a, b)) /
                            (math.hypot(*a) * math.hypot(*b)), math.sqrt(.5))


def test_native_goal_rejection_continues_to_a_different_arrival():
    attempts = []
    def accept(edges):
        attempts.append(edges)
        return len(attempts) > 1
    result = search(goal_accept=accept)
    assert result['accepted'], result
    assert result['goal_rejections'] == 1
    assert len(attempts) == 2
    assert attempts[0] != result['edges']


def test_expired_clock_calls_no_geometry_or_acceptance_callbacks():
    def forbidden(*args):
        pytest.fail('callback invoked after deadline')
    result = search(deadline=time.monotonic() - 1, edge_clear=forbidden,
                    portal=forbidden, goal_accept=forbidden)
    assert not result['accepted']
    assert result['reason'] == 'deadline expired'
    assert result['expanded'] == 0


def test_bounds_and_expansion_cap_have_distinct_failure_reasons():
    exhausted = search(bounds_nm=(0, 0, 12, 0), edge_clear=lambda *args: False)
    capped = search(max_expansions=1)
    assert not exhausted['accepted'] and exhausted['reason'] == 'frontier exhausted'
    assert not capped['accepted'] and capped['reason'] == 'expansion cap'
    assert capped['expanded'] == 1


def test_goal_and_start_layer_restrictions_are_honored():
    result = search(start_layers=(2,), goal_layers=(2,))
    assert result['accepted'], result
    assert all(e['layer'] == 2 and e['end_layer'] == 2 for e in result['edges'])
    assert result['cost_nm'] == 24


def test_secondary_face_cost_changes_route_choice():
    result = search(start_layers=(0, 2), goal_layers=(0, 2))
    assert result['accepted']
    assert all(e['layer'] == 0 for e in result['edges'])
    assert result['cost_nm'] == 12


def test_queries_are_cached_and_every_track_is_checked_exactly():
    queries = set()
    def clear(a, z, layer):
        key = (a, z, layer)
        assert key not in queries, 'duplicate geometry callback'
        queries.add(key)
        return not hits_box(a, z, (6, -3, 8, 3))
    result = search(edge_clear=clear)
    assert result['accepted'], result
    assert all((e['start'], e['end'], e['layer']) in queries for e in result['edges'])


@pytest.mark.parametrize('kwargs', [dict(step_nm=0), dict(minimum_run_nm=-1),
    dict(secondary_factor=.5), dict(bend_cost_nm=-1), dict(max_expansions=0),
    dict(goal=(12.5, 0)), dict(goal=(13, 0), step_nm=2), dict(start_heading=(2, 0)),
    dict(layers=(0, 0)), dict(start_layers=(3,)), dict(bounds_nm=(1, 0, -1, 12)),
    dict(deadline=float('nan'))])
def test_invalid_numerical_or_layer_arguments_are_rejected(kwargs):
    with pytest.raises(ValueError):
        search(**kwargs)


def test_final_run_cannot_end_early_after_source_or_portal():
    short = search(goal=(1, 0), bounds_nm=(0, 0, 1, 0))
    assert not short['accepted'] and short['reason'] == 'frontier exhausted'
    def portal(point, heading, layer):
        if point == (2, 0) and layer == 0:
            return dict(end=(11, 0), end_layer=2, cost_nm=1, payload=None)
    short = search(bounds_nm=(0, 0, 12, 0), goal_layers=(2,), portal=portal)
    assert not short['accepted'] and short['reason'] == 'frontier exhausted'


def test_out_of_bounds_moves_never_reach_geometry_callback():
    def clear(a, z, layer):
        assert 0 <= a[0] <= 12 and 0 <= z[0] <= 12
        assert a[1] == z[1] == 0
        return True
    result = search(bounds_nm=(0, 0, 12, 0), edge_clear=clear)
    assert result['accepted'], result


def test_portal_queries_are_cached_across_run_states():
    queries = set()
    def portal(point, heading, layer):
        key = (point, heading, layer)
        assert key not in queries, 'duplicate portal callback'
        queries.add(key)
        return None
    result = search(portal=portal,
                    edge_clear=lambda a, z, layer: not hits_box(a, z, (6, -3, 8, 3)))
    assert result['accepted'], result


@pytest.mark.parametrize('end,end_layer,cost', [((1, 1), 2, 1), ((-1, 0), 2, 1),
    ((1, 0), 0, 1), ((1, 0), 2, 0), ((1, 0), 2, -1), ((1, 0), 3, 1)])
def test_invalid_portals_are_rejected(end, end_layer, cost):
    with pytest.raises(ValueError):
        search(portal=lambda *args: dict(end=end, end_layer=end_layer, cost_nm=cost))
