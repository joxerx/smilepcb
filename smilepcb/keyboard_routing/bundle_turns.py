"""Measure constant-pitch turns on saved copper; never waive physical rules.

Credits belong to distinct same-role nets on the same face, with continuous
support on both sides of the corner. A bundle maneuver costs once; its member
corners are credited against independent-bend penalties. Straight copper is
still cheaper than an otherwise unnecessary coordinated detour.
"""
from collections import defaultdict
import math

from .role_quality import _runs, _clip, role_coverage


def _corners(tracks, nets, bounds):
    # A collinear run can cross a corner without ending there; a third
    # arm can also have another width. Determine actual centerline incidence
    # across all widths before treating any point as a degree-two turn.
    runs_by_net = defaultdict(list)
    vertices = defaultdict(set)
    support = _runs(tracks, nets)
    # Native input is not guaranteed octilinear: every physical straight arm
    # participates in incidence, even if it cannot earn parallel-run coverage.
    physical = [t for t in tracks.values() if t['class'] == 'PCB_TRACK'
                and t['net'] in nets and tuple(t['start']) != tuple(t['end'])]
    for t in [*support, *physical]:
        key = t['net'], t['layer']
        runs_by_net[key].append(t)
        vertices[key].update((tuple(t['start']), tuple(t['end'])))
    rays = {}
    for (net, layer), points in vertices.items():
        for q in points:
            if not bounds[0] <= q[0] <= bounds[2] or not bounds[1] <= q[1] <= bounds[3]:
                continue
            directions = {}
            widths = set()
            for t in runs_by_net[net, layer]:
                a, z = tuple(t['start']), tuple(t['end'])
                delta = z[0] - a[0], z[1] - a[1]
                cross = delta[0] * (q[1] - a[1]) - delta[1] * (q[0] - a[0])
                if cross or any(not min(a[k], z[k]) <= q[k] <= max(a[k], z[k]) for k in (0, 1)):
                    continue
                widths.add(t['width'])
                for other in (a, z):
                    dx, dy = other[0] - q[0], other[1] - q[1]
                    g = math.gcd(dx, dy)
                    if not g:
                        continue
                    part = _clip(q, other, bounds)
                    if part is None:
                        continue
                    h = dx // g, dy // g
                    directions[h] = max(directions.get(h, 0), math.dist(*part))
            # A width-changing corner is measurable but has no uniform-pitch
            # turn credit. Zero is an eligibility sentinel, not copper width.
            width = next(iter(widths)) if len(widths) == 1 else 0
            rays[net, layer, width, q] = directions
    raw = 0
    eligible = defaultdict(list)
    for (net, layer, width, q), directions in sorted(rays.items()):
        if len(directions) != 2:
            continue
        a, z = sorted(directions)
        if a == tuple(-v for v in z):
            continue
        raw += 1
        cosine = sum(a[k] * z[k] for k in (0, 1)) / (math.hypot(*a) * math.hypot(*z))
        if not width or abs(cosine + math.sqrt(.5)) > 1e-8:
            continue  # 90-degree or acute corners earn no positive credit.
        unit = tuple(tuple(v / math.hypot(*h) for v in h) for h in (a, z))
        eligible[layer, a, z].append(dict(net=net, width=width, point=q,
            directions=unit, lengths=(directions[a], directions[z])))
    return raw, eligible


def bundle_turns(tracks, nets, bounds, *, min_run_nm=2000000,
                 pitch_tolerance_nm=25000, maximum_gap_nm=1000000,
                 preferred_gap_nm=198000):
    """Return split/duplicate-invariant turn diagnostics in a fixed crop.

    Directed rays point OUT of each corner, so a true miter offset has opposite
    signed normal distances on its two arms. Equal absolute normal distances
    distinguish a parallel turn from merely similar-looking slopes.
    """
    raw, cohorts = _corners(tracks, set(nets), bounds)
    shared = maneuvers = 0
    weighted_groups = 0.
    members = 0
    for corners in cohorts.values():
        parent = list(range(len(corners)))
        weights = [0.] * len(corners)

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, a in enumerate(corners):
            for j in range(i + 1, len(corners)):
                b = corners[j]
                if a['net'] == b['net']:
                    continue
                delta = tuple(b['point'][k] - a['point'][k] for k in (0, 1))
                if math.hypot(*delta) > 2 * (maximum_gap_nm + (a['width'] + b['width']) / 2):
                    continue
                offsets = [-h[1] * delta[0] + h[0] * delta[1] for h in a['directions']]
                if offsets[0] * offsets[1] >= 0:
                    continue
                if abs(abs(offsets[0]) - abs(offsets[1])) > pitch_tolerance_nm:
                    continue
                gaps = [abs(v) - (a['width'] + b['width']) / 2 for v in offsets]
                if min(gaps) < 0 or max(gaps) > maximum_gap_nm:
                    continue
                supported = True
                for k, h in enumerate(a['directions']):
                    along = sum(delta[n] * h[n] for n in (0, 1))
                    overlap = min(a['lengths'][k], along + b['lengths'][k]) - max(0., along)
                    if overlap + 1e-6 < min_run_nm:
                        supported = False
                        break
                if not supported:
                    continue
                gap = max(gaps)
                weight = 1 / (1 + max(0., gap - preferred_gap_nm) /
                    max(1., preferred_gap_nm + (a['width'] + b['width']) / 2))
                weights[i] = max(weights[i], weight)
                weights[j] = max(weights[j], weight)
                parent[root(j)] = root(i)
        groups = defaultdict(list)
        for i, weight in enumerate(weights):
            if weight:
                groups[root(i)].append(i)
        # A folded same-net path is not several independent bundle members.
        # Reject ambiguous components rather than award duplicated ownership.
        groups = [indices for indices in groups.values()
                  if len({corners[i]['net'] for i in indices}) == len(indices)]
        maneuvers += len(groups)
        weighted_groups += sum(max(weights[i] for i in indices) for indices in groups)
        members += sum(len(indices) for indices in groups)
        shared += sum(weights[i] for indices in groups for i in indices)
    return dict(raw_bends=raw, bundle_maneuvers=maneuvers,
                weighted_bundle_maneuvers=weighted_groups,
                shared_bend_equivalents=shared, bundled_member_bends=members,
                isolated_bends=raw - members)


def bundle_objective(tracks, role_nets, bounds):
    """Comparable nm-equivalent objective; all safety checks stay external.

    Parallel-run coverage is the unchanged V2 metric. Credit is positive but
    cannot cancel the whole cost of an extra turn. Same-net segments and extra
    pair combinations never create extra bundle rewards.
    """
    coverage = role_coverage(tracks, role_nets, bounds)
    result = {}
    for role, nets in role_nets.items():
        t = bundle_turns(tracks, nets, bounds)
        length = sum(math.dist(*pair) for run in _runs(tracks, set(nets))
                     for pair in [_clip(run['start'], run['end'], bounds)] if pair)
        credit = 750000 * t['shared_bend_equivalents'] - 250000 * t['weighted_bundle_maneuvers']
        residual = 750000 * t['raw_bends'] - credit
        result[role] = dict(**t, turn_credit_nm=credit, residual_turn_cost_nm=residual,
                            length_nm=length, coverage_nm=coverage[role],
                            score_nm=coverage[role] - residual - .1 * length)
    return result
