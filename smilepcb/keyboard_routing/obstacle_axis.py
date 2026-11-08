"""Deterministic, board-independent A* for a finite-width corridor axis.

Geometry callbacks own all band, corner and coupled-via clearance decisions.
The search owns only the octilinear lattice and positive geometric costs. Its
Euclidean heuristic is admissible: a portal pays its displacement length plus
``cost_nm`` (a positive surcharge). This is graph search, not enumeration of all
path histories: Native rejection of a goal continues with other arrival states,
without a completeness guarantee for history-dependent final admission.
"""
from __future__ import annotations

import heapq
import itertools
import math
from numbers import Integral, Real
import time


_DIRECTIONS = ((1, 0), (1, 1), (0, 1), (-1, 1),
               (-1, 0), (-1, -1), (0, -1), (1, -1))


def _integer(value, name, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f'{name} must be an integer')
    if minimum is not None and value < minimum:
        raise ValueError(f'{name} must be >= {minimum}')
    return int(value)


def _number(value, name, *, minimum=None, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
    if minimum is not None and value < minimum or positive and value <= 0:
        raise ValueError(f'{name} is out of range')
    return float(value)


def _point(value, name):
    try:
        if len(value) != 2:
            raise ValueError(f'{name} must contain two integers')
        return tuple(_integer(v, name) for v in value)
    except TypeError as error:
        raise ValueError(f'{name} must contain two integers') from error


def search_corridor(start, goal, *, start_heading, layers, preferred_layer,
                    bounds_nm, step_nm, minimum_run_nm, edge_clear, portal,
                    deadline, start_layers=None, goal_layers=None,
                    goal_accept=None, max_expansions=80000,
                    bend_cost_nm=1500000, secondary_factor=2.0, admission_deadline=None):
    """Search a bounded lattice, returning diagnostics and accepted axis edges.

    ``deadline`` is an absolute ``time.monotonic()`` time. Start seeds default
    to the preferred face; goals default to either face. A straight run starts
    at zero both at the source and after every portal. Turns change heading by
    exactly 45 degrees and advance enough cells to establish their outgoing
    run; incoming and final runs must also meet ``minimum_run_nm``.

    ``edge_clear(a, z, layer)`` checks the *entire exact track segment*. Portal
    callbacks validate the entire coupled transition themselves. Their result
    is None or a mapping with end, end_layer, positive cost_nm and opaque
    payload; the endpoint must be forward on the same heading and other face.
    A started goal admission may finish by ``admission_deadline`` (default:
    ``deadline``). Rejected admission and all geometry expansion retain the
    original search deadline. No board objects are imported or mutated here.
    """
    began = time.monotonic()
    start, goal = _point(start, 'start'), _point(goal, 'goal')
    heading = _point(start_heading, 'start_heading')
    if heading not in _DIRECTIONS:
        raise ValueError('start_heading must be an octilinear signed unit vector')
    step_nm = _integer(step_nm, 'step_nm', minimum=1)
    minimum_run_nm = _number(minimum_run_nm, 'minimum_run_nm', minimum=0)
    bend_cost_nm = _number(bend_cost_nm, 'bend_cost_nm', minimum=0)
    secondary_factor = _number(secondary_factor, 'secondary_factor', minimum=1)
    deadline = _number(deadline, 'deadline')
    admission_deadline = deadline if admission_deadline is None else _number(admission_deadline, 'admission_deadline')
    max_expansions = _integer(max_expansions, 'max_expansions', minimum=1)
    try:
        layers = tuple(_integer(v, 'layers') for v in layers)
        bounds = tuple(_integer(v, 'bounds_nm') for v in bounds_nm)
    except TypeError as error:
        raise ValueError('layers and bounds_nm must be sequences') from error
    if len(layers) != 2 or len(set(layers)) != 2:
        raise ValueError('layers must contain two distinct integer IDs')
    preferred_layer = _integer(preferred_layer, 'preferred_layer')
    if preferred_layer not in layers:
        raise ValueError('preferred_layer must be one of layers')
    if len(bounds) != 4 or bounds[0] > bounds[2] or bounds[1] > bounds[3]:
        raise ValueError('bounds_nm must be xmin, ymin, xmax, ymax')
    if any((goal[i] - start[i]) % step_nm for i in (0, 1)):
        raise ValueError('start/goal displacement must be a multiple of step_nm')
    if not callable(edge_clear) or portal is not None and not callable(portal):
        raise ValueError('edge_clear and optional portal must be callable')
    if goal_accept is not None and not callable(goal_accept):
        raise ValueError('goal_accept must be callable')

    def selected(values, default, name):
        try:
            chosen = tuple(_integer(v, name) for v in (default if values is None else values))
        except TypeError as error:
            raise ValueError(f'{name} must be a sequence') from error
        if not chosen or any(v not in layers for v in chosen):
            raise ValueError(f'{name} must select at least one known layer')
        return tuple(v for v in layers if v in chosen)

    seeds = selected(start_layers, (preferred_layer,), 'start_layers')
    goals = selected(goal_layers, layers, 'goal_layers')

    def inside(point):
        return bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]

    if not inside(start) or not inside(goal):
        raise ValueError('start and goal must be inside bounds_nm')

    lengths = tuple(step_nm * math.hypot(*h) for h in _DIRECTIONS)
    needed = tuple(max(1, math.ceil(minimum_run_nm / length)) for length in lengths)
    # run counts are capped separately for each heading, so no floating-point
    # accumulation changes equality or creates an unbounded state dimension.
    caps = tuple(math.ceil(minimum_run_nm / length) for length in lengths)
    edge_cache, portal_cache = {}, {}
    serial = itertools.count()
    queue, costs, parents = [], {}, {}
    expanded = generated = rejections = 0

    def heuristic(state):
        return math.hypot(goal[0] - state[0], goal[1] - state[1])

    for layer in seeds:
        state = (*start, _DIRECTIONS.index(heading), layer, 0)
        costs[state] = 0.
        heapq.heappush(queue, (heuristic(state), 0., next(serial), state))
        generated += 1

    def result(accepted, reason, edges=None, cost=None):
        return dict(accepted=accepted, edges=edges or [], reason=reason,
                    expanded=expanded, generated=generated,
                    goal_rejections=rejections, elapsed_s=time.monotonic() - began,
                    cost_nm=cost)

    def reconstruct(state):
        edges = []
        while state in parents:
            previous, kind, payload = parents[state]
            edges.append(dict(start=previous[:2], end=state[:2],
                              layer=previous[3], end_layer=state[3],
                              kind=kind, payload=payload))
            state = previous
        edges.reverse()
        return edges

    def enqueue(previous, state, price, kind, payload=None):
        cost = costs[previous] + price
        if cost >= costs.get(state, math.inf):
            return
        costs[state] = cost
        parents[state] = (previous, kind, payload)
        heapq.heappush(queue, (cost + heuristic(state), cost, next(serial), state))
        nonlocal generated
        generated += 1

    while queue:
        if time.monotonic() >= deadline:
            return result(False, 'deadline expired')
        if expanded >= max_expansions:
            return result(False, 'expansion cap')
        _, cost, _, state = heapq.heappop(queue)
        if cost != costs.get(state):
            continue
        expanded += 1
        a, index, layer, run = state[:2], state[2], state[3], state[4]
        if a == goal and layer in goals and run >= caps[index]:
            edges = reconstruct(state)
            if time.monotonic() >= deadline:
                return result(False, 'deadline expired')
            admitted = goal_accept is None or goal_accept([dict(e) for e in edges])
            if time.monotonic() >= (admission_deadline if admitted else deadline):
                return result(False, 'deadline expired')
            if admitted:
                return result(True, None, edges, cost)
            rejections += 1

        for turn in (0, -1, 1):
            if turn and run < caps[index]:
                continue
            direction = (index + turn) % 8
            h = _DIRECTIONS[direction]
            units = needed[direction] if turn else 1
            z = (a[0] + h[0] * units * step_nm,
                 a[1] + h[1] * units * step_nm)
            if not inside(z):
                continue
            new_run = min(caps[direction], units if turn else run + units)
            new = (*z, direction, layer, new_run)
            factor = 1. if layer == preferred_layer else secondary_factor
            price = lengths[direction] * units * factor + (bend_cost_nm if turn else 0.)
            if cost + price >= costs.get(new, math.inf):
                continue
            if time.monotonic() >= deadline:
                return result(False, 'deadline expired')
            key = (a, z, layer)
            if key not in edge_cache:
                edge_cache[key] = bool(edge_clear(a, z, layer))
            if time.monotonic() >= deadline:
                return result(False, 'deadline expired')
            if edge_cache[key]:
                enqueue(state, new, price, 'track')

        if portal is None:
            continue
        if time.monotonic() >= deadline:
            return result(False, 'deadline expired')
        key = (a, _DIRECTIONS[index], layer)
        if key not in portal_cache:
            portal_cache[key] = portal(*key)
        if time.monotonic() >= deadline:
            return result(False, 'deadline expired')
        proposal = portal_cache[key]
        if proposal is None:
            continue
        z = _point(proposal['end'], 'portal end')
        end_layer = _integer(proposal['end_layer'], 'portal end_layer')
        surcharge = _number(proposal['cost_nm'], 'portal cost_nm', positive=True)
        delta = (z[0] - a[0], z[1] - a[1])
        h = _DIRECTIONS[index]
        axis = 0 if h[0] else 1
        units = delta[axis] // (h[axis] * step_nm)
        if (units <= 0 or delta != (h[0] * units * step_nm, h[1] * units * step_nm)
                or end_layer not in layers or end_layer == layer):
            raise ValueError('portal must advance on heading to the other layer on the lattice')
        if inside(z):
            factor = 1. if layer == preferred_layer else secondary_factor
            enqueue(state, (*z, index, end_layer, 0),
                    math.hypot(*delta) * factor + surcharge,
                    'portal', proposal.get('payload'))

    if time.monotonic() >= deadline:
        return result(False, 'deadline expired')
    return result(False, 'frontier exhausted')
