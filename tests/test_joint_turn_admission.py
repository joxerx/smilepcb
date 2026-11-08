"""Optional joined-edge and complete-plan admission in the pure joint search."""
import time

from smilepcb.keyboard_routing.joint_repair import search_paths


def smooth(incoming, outgoing, layer):
    dot = sum(a * b for a, b in zip(incoming, outgoing))
    return dot > 0 and 2 * dot * dot >= sum(a * a for a in incoming) * sum(b * b for b in outgoing)


def search(edges, starts, goal, **kwargs):
    points = {point for a, b, layer in edges for point in (a, b)}
    def edge(a, b, layer):
        cost = edges.get((a, b, layer))
        return None if cost is None else {'path': [a, b], 'cost': cost, 'blockers': set()}
    return search_paths(points, starts, [goal], edge, lambda point: None,
                        soft_ids=set(), deadline=time.monotonic() + 2,
                        max_results=1, max_vias=0, **kwargs)


def test_optional_turn_admission_rejects_joined_right_angle_without_changing_default():
    a, b, goal = (0, 0), (2, 0), (2, 2)
    edges = {(a, b, 0): 2, (b, goal, 0): 2}
    assert len(search(edges, [(a, 0)], (goal, 0))) == 1
    assert search(edges, [(a, 0)], (goal, 0), turn_admission=smooth) == []


def test_search_reaches_alternate_45_degree_route_after_rejecting_right_angle():
    a, bad, good, goal = (0, 0), (2, 0), (1, 1), (2, 2)
    edges = {(a, bad, 0): 1, (bad, goal, 0): 1,
             (a, good, 0): 3, (good, goal, 0): 3}
    result = search(edges, [(a, 0)], (goal, 0), turn_admission=smooth)
    assert len(result) == 1
    assert result[0]['legs'] == [(0, [a, good]), (0, [good, goal])]


def test_distinct_arrival_headings_survive_label_and_frontier_caps():
    junction, goal, good = (0, 0), (1, 0), (-10, -10)
    bad = [(0, -n) for n in range(1, 9)]
    edges = {(point, junction, 0): n for n, point in enumerate(bad, 1)}
    edges.update({(good, junction, 0): 100, (junction, goal, 0): 1})
    result = search(edges, [(point, 0) for point in [*bad, good]], (goal, 0),
                    turn_admission=smooth)
    assert len(result) == 1
    assert result[0]['legs'] == [(0, [good, junction]), (0, [junction, goal])]


def test_rejected_goal_does_not_consume_result_cap():
    a, middle, goal = (0, 0), (1, 1), (2, 0)
    edges = {(a, goal, 0): 1, (a, middle, 0): 2, (middle, goal, 0): 2}
    result = search(edges, [(a, 0)], (goal, 0),
                    plan_admission=lambda proposal: len(proposal['legs']) == 2)
    assert len(result) == 1
    assert result[0]['legs'] == [(0, [a, middle]), (0, [middle, goal])]


def test_explicit_via_resets_same_layer_arrival_heading():
    a, middle, goal = (0, 0), (1, 0), (1, 1)
    edges = {(a, middle, 0), (middle, goal, 2)}
    def edge(x, y, layer):
        return {'path': [x, y], 'cost': 1, 'blockers': set()} if (x, y, layer) in edges else None
    result = search_paths([a, middle, goal], [(a, 0)], [(goal, 2)], edge,
                          lambda point: set() if point == middle else None,
                          soft_ids=set(), deadline=time.monotonic() + 2,
                          max_results=1, max_vias=1, turn_admission=smooth)
    assert len(result) == 1
    assert result[0]['vias'] == [middle]
    assert result[0]['legs'] == [(0, [a, middle]), (2, [middle, goal])]
