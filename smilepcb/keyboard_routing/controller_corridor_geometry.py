"""Pure, bounded proposals from real controller ports to real field pads.

These are geometry proposals, not a clearance or Native feasibility proof.
The caller must admit the complete cohort before installing any copper.
"""
from collections import defaultdict
import hashlib
from itertools import zip_longest
import math
import time

from .bundle_geometry import paths_clear
from .corridor_geometry import offset_path
from .follow_geometry import follow_spans, simplify
from .free_corridor_geometry import corridor_candidates
from .polish_geometry import octile, valid_shape


def _point(value):
    if not isinstance(value, (tuple, list)) or len(value) != 2 or any(type(x) is not int for x in value):
        raise ValueError('Integer-nanometre point required')
    return tuple(value)


def _heading(origin, source):
    dx, dy = source[0]-origin[0], source[1]-origin[1]
    scale = math.gcd(dx, dy)
    if not scale or max(abs(dx//scale), abs(dy//scale)) != 1:
        raise ValueError('Physical octilinear departure heading required')
    return dx//scale, dy//scale


def _freeze(value):
    return tuple(_freeze(x) for x in value) if isinstance(value, (tuple, list)) else value


def _source_key(pad_ids):
    pads = list(pad_ids)
    if not pads or any(type(pad) is not str or not pad for pad in pads) or len(set(pads)) != len(pads):
        raise ValueError('Distinct real source pads required')
    return tuple(sorted(pads))


def _compatible(a, b):
    dot = sum(x*y for x, y in zip(a, b))
    return dot > 0 and dot*dot >= .16*sum(x*x for x in a)*sum(x*x for x in b)


def group_corridors(ports, target_sets, *, eligible_nets, pending_nets=None, max_members=6):
    """Partition eligible physical neighbours, retaining closed cohort members.

    Net names never determine ordering or membership. Pending nets filter whole
    physical groups only after all eligible neighbours have been considered.
    A length-one group is an explicit fallback, not a bundle.
    """
    if type(max_members) is not int or not 1 <= max_members <= 6:
        raise ValueError('Group resource cap must be 1..6')
    eligible = set(eligible_nets)
    pending = None if pending_nets is None else set(pending_nets)
    seen_sources, source_pads, owners, locations = set(), set(), {}, set()
    target_geometry = {}
    banks = defaultdict(list)

    def claim(pad, net):
        if pad is None or pad == '':
            raise ValueError('Real physical pad identity required')
        if pad in owners and owners[pad] != net:
            raise ValueError('Duplicate physical pad ownership')
        owners[pad] = net

    for port in ports:
        net = port['net']
        if net not in eligible:
            continue
        source = _point(port['exit_nm'])
        heading = _heading(_point(port['heading_origin_nm']), source)
        face = port['exit_layer']
        if type(face) is not int or face not in (0, 2):
            raise ValueError('Supported copper face required')
        if (source, face) in locations:
            raise ValueError('Duplicate physical source point')
        locations.add((source, face))
        pad_ids = list(port['pad_ids'])
        source_key = _source_key(pad_ids)
        if source_key in seen_sources or source_pads.intersection(source_key):
            raise ValueError('Duplicate or overlapping physical source pads')
        seen_sources.add(source_key)
        source_pads.update(source_key)
        for pad in pad_ids:
            claim(pad, net)
        targets = []
        for target in target_sets.get(net, ()):
            point = _point(target['point'])
            layers = tuple(sorted(set(target['layers'])))
            if not layers or any(type(x) is not int or x not in (0, 2) for x in layers):
                raise ValueError('Supported target copper faces required')
            claim(target['pad_id'], net)
            pad = target['pad_id']
            if pad in target_geometry and target_geometry[pad] != (point, layers):
                raise ValueError('Stale conflicting physical target pad geometry')
            target_geometry[pad] = (point, layers)
            if point == source:
                raise ValueError('Stale target at source point')
            vector = tuple(point[i]-source[i] for i in (0, 1))
            projection = sum(vector[i]*heading[i] for i in (0, 1))
            # Prefer an exposed forward pad, then the closest physical pad.
            # Coordinates break ties, never signal names or input enumeration.
            key = (projection <= 0, sum(x*x for x in vector), -projection, point, layers, str(target['pad_id']))
            targets.append((key, point, layers, target['pad_id']))
        if not targets:
            raise ValueError('Actual field target set required for each eligible port')
        _, target, layers, target_pad = min(targets)
        bank = _freeze(port['bank'])
        row = dict(net=net, source=source, target=target, target_pad_id=target_pad,
                   target_layers=list(layers), source_layer=face, layer=face,
                   path=[source, target], pad_ids=pad_ids, source_key=list(source_key), bank=bank, heading=heading)
        if 'departure_waypoint_nm' in port:
            row['departure_waypoint_nm'] = _departure_waypoint(source, heading, port['departure_waypoint_nm'])
        banks[bank].append(row)

    groups = []
    # A shared physical bank is traversed before splitting on face/direction;
    # otherwise opposite-going or opposite-face intervening pads get skipped.
    for bank, rows in banks.items():
        # Production banks encode the physical package face (ref, axis, sign),
        # even when an individual departure has already turned. Generic bank
        # metadata falls back to the deterministic physical-source anchor.
        if (isinstance(bank, tuple) and len(bank) == 3 and type(bank[1]) is int and
                bank[1] in (0, 1) and type(bank[2]) is int and bank[2] in (-1, 1)):
            heading = (bank[2], 0) if bank[1] == 0 else (0, bank[2])
        else:
            heading = min(rows, key=lambda r: r['source'])['heading']
        tangent = (-heading[1], heading[0])
        rows.sort(key=lambda r: (sum(r['source'][i]*tangent[i] for i in (0, 1)), r['source']))
        current = []
        for row in rows:
            vector = tuple(row['target'][i]-row['source'][i] for i in (0, 1))
            if current and (len(current) == max_members or row['heading'] != current[0]['heading'] or
                    row['source_layer'] != current[0]['source_layer'] or
                    not all(_compatible(vector, tuple(q['target'][i]-q['source'][i] for i in (0, 1))) for q in current)):
                groups.append(current)
                current = []
            current.append(row)
        if current:
            groups.append(current)
    groups.sort(key=lambda g: (g[0]['source'], g[0]['heading'], g[0]['source_layer']))
    return [g for g in groups if pending is None or any(r['net'] in pending for r in g)]


def _bounds(value):
    if len(value) == 4 and all(type(x) is int for x in value):
        low, high = (value[0], value[1]), (value[2], value[3])
    else:
        low, high = map(_point, value)
    if any(low[i] >= high[i] for i in (0, 1)):
        raise ValueError('Positive physical bounds required')
    return low, high


def _rail_spines(group, board, obstacles, radius):
    start = tuple(round(sum(r['source'][i] for r in group)/len(group)) for i in (0, 1))
    end = tuple(round(sum(r['target'][i] for r in group)/len(group)) for i in (0, 1))
    axis = 0 if abs(end[0]-start[0]) >= abs(end[1]-start[1]) else 1
    cross = 1-axis
    sign = 1 if end[axis] > start[axis] else -1
    rails = {board[0][cross]+radius, board[1][cross]-radius}
    for low, high in obstacles:
        rails.update((low[cross]-radius, high[cross]+radius))
    for rail in sorted(rails):
        if not board[0][cross]+radius <= rail <= board[1][cross]-radius:
            continue
        left, right = list(start), list(end)
        left[axis] += sign*abs(rail-start[cross])
        right[axis] -= sign*abs(rail-end[cross])
        left[cross] = right[cross] = rail
        if sign*(right[axis]-left[axis]) <= 0:
            continue
        spine = simplify([start, tuple(left), tuple(right), end])
        if valid_shape(spine):
            yield spine


def _return_spines(group, board, obstacles, radius, deadline):
    """Finite cardinal returns beyond a fixed forward launch, using old offsets."""
    heading=tuple(group[0]['heading'])
    if heading not in ((1,0),(-1,0),(0,1),(0,-1)) or any(tuple(r['heading'])!=heading for r in group):
        return
    tangent=(-heading[1],heading[0])
    project=lambda p,d:sum(p[i]*d[i] for i in (0,1))
    launches=[r.get('departure_waypoint_nm',r['source']) for r in group]
    if any(project(r['target'],heading)>=project(p,heading) for r,p in zip(group,launches)):
        return
    start_n=max(project(p,heading) for p in launches)
    start_t=round(sum(project(p,tangent) for p in launches)/len(launches))
    targets_n=[project(r['target'],heading) for r in group]
    targets_t=[project(r['target'],tangent) for r in group]
    end_n=max(targets_n)+radius
    corners=[(x,y) for x in (board[0][0],board[1][0]) for y in (board[0][1],board[1][1])]
    low_n,high_n=min(project(p,heading) for p in corners),max(project(p,heading) for p in corners)
    low_t,high_t=min(project(p,tangent) for p in corners),max(project(p,tangent) for p in corners)
    rails={start_n+2*radius,high_n-radius}
    for low,high in obstacles:
        if time.monotonic()>=deadline:return
        values=[project((x,y),heading) for x in (low[0],high[0]) for y in (low[1],high[1])]
        rails.update((min(values)-radius,max(values)+radius))
    preferred=1 if sum(targets_t)/len(targets_t)>=start_t else -1
    point=lambda n,t:tuple(heading[i]*n+tangent[i]*t for i in (0,1))
    for rail in sorted(rails):
        if time.monotonic()>=deadline:return
        if rail<start_n+2*radius or not low_n+radius<=rail<=high_n-radius or end_n>=rail-radius:
            continue
        for turn in (preferred,-preferred):
            # Two chamfers and a radius-long common straight keep the
            # inner offset from consuming the lateral return segment.
            lateral=turn*(max([turn*project(p,tangent) for p in launches]+[turn*t for t in targets_t])+3*radius)
            if not low_t+radius<=lateral<=high_t-radius:continue
            spine=[point(start_n,start_t),point(rail-radius,start_t),
                   point(rail,start_t+turn*radius),point(rail,lateral-turn*radius),
                   point(rail-radius,lateral),point(end_n,lateral)]
            if valid_shape(spine):yield spine


def _departure_compatible(path, heading):
    if not path:
        return False
    source = path[0]
    end = next((p for p in path[1:] if p != source), None)
    if end is None:
        return False
    leg = tuple(end[i]-source[i] for i in (0, 1))
    dot = sum(heading[i]*leg[i] for i in (0, 1))
    return dot > 0 and 2*dot*dot >= sum(v*v for v in heading)*sum(v*v for v in leg)


def _departure_waypoint(source, heading, waypoint):
    waypoint = _point(waypoint)
    delta = tuple(waypoint[i]-source[i] for i in (0, 1))
    if (sum(delta[i]*heading[i] for i in (0, 1)) <= 0 or
            delta[0]*heading[1] != delta[1]*heading[0]):
        raise ValueError('Departure waypoint must lie forward on the physical heading')
    return waypoint


def _attach(body, source, target, width, *, heading, departure_waypoint_nm=None):
    original_source = source
    if departure_waypoint_nm is not None:
        source = _departure_waypoint(source, heading, departure_waypoint_nm)
    choices = []
    # Nearest projection can force a perpendicular microscopic launch. Clip
    # the same body at its exact intersections with the three allowed source
    # rays, retaining bounded analytic attachments rather than a new search.
    directions = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
    index = directions.index(tuple(heading))
    rays = [directions[(index+turn) % len(directions)] for turn in (-1, 0, 1)]
    cross = lambda a, b: a[0]*b[1]-a[1]*b[0]
    bases = [body, *follow_spans(body, source, target)]
    spans = list(bases)
    for base in bases:
        for i, (a, b) in enumerate(zip(base, base[1:])):
            segment = (b[0]-a[0], b[1]-a[1])
            delta = (a[0]-source[0], a[1]-source[1])
            for ray in rays:
                det = cross(ray, segment)
                if not det:
                    continue
                distance, along = cross(delta, segment), cross(delta, ray)
                if det < 0:
                    det, distance, along = -det, -distance, -along
                if distance < 0 or not 0 <= along <= det or distance % det:
                    continue
                point = tuple(source[k]+distance//det*ray[k] for k in (0, 1))
                span = simplify([point, *base[i+1:]])
                if len(span) > 1:
                    spans.append(span)
    for span in spans:
        for left in octile(source, span[0]):
            for right in octile(span[-1], target):
                tail = simplify([*left, *span[1:], *right[1:]])
                path = [original_source, *tail] if departure_waypoint_nm is not None else tail
                if _departure_compatible(tail, heading) and valid_shape(path) and paths_clear([path], [width], 0):
                    choices.append((path, span))
    return min(choices, key=lambda q: (len(q[0]), sum(math.dist(a, b) for a, b in zip(q[0], q[0][1:])), q)) if choices else None


def corridor_proposals(group, *, track_width_nm, clearance_nm, board_bounds_nm,
                       obstacle_bounds=(), deadline, max_candidates=48, via_width_nm=None,
                       via_envelope_radius_nm=None):
    """Offer finite common-rail and existing-helper candidates on one face.

    Capsule records reserve the union of the actual proposed lane paths for
    its own lane owner; they never reserve the paths' bounding rectangle.
    Terminals may require caller-proved layer bridges. No clearance is claimed.
    """
    if type(track_width_nm) is not int or track_width_nm <= 0 or type(clearance_nm) is not int or clearance_nm <= 0:
        raise ValueError('Positive integer physical width and clearance required')
    if via_width_nm is not None and (type(via_width_nm) is not int or via_width_nm <= 0):
        raise ValueError('Optional physical via width must be a positive integer')
    if via_envelope_radius_nm is not None and (type(via_envelope_radius_nm) is not int or via_envelope_radius_nm <= 0):
        raise ValueError('Optional reserved via radius must be a positive integer')
    if type(max_candidates) is not int or not 1 <= max_candidates <= 256:
        raise ValueError('Candidate limit must be 1..256')
    if not math.isfinite(deadline):
        raise ValueError('Finite monotonic deadline required')
    if time.monotonic() >= deadline:
        return []
    if not 1 <= len(group) <= 6:
        raise ValueError('One to six distinct physical lanes required')
    board = _bounds(board_bounds_nm)
    obstacles = tuple(_bounds(b) for b in obstacle_bounds)
    face = group[0]['source_layer']
    if face not in (0, 2) or any(r['source_layer'] != face for r in group):
        raise ValueError('A common physical copper face is required')
    # Generate both our rails and the inherited helper's candidates in one
    # physical-source frame. Rounding local means/projections then commutes
    # with every integer translation, including half-nm centroid ties.
    origin = _point(group[0]['source'])

    def local(point):
        return tuple(point[i]-origin[i] for i in (0, 1))

    def global_point(point):
        return tuple(point[i]+origin[i] for i in (0, 1))

    rows = []
    source_keys, source_pads, source_points = set(), set(), set()
    for row in group:
        source, target = _point(row['source']), _point(row['target'])
        source_key = _source_key(row['pad_ids'])
        if (source_key in source_keys or source_pads.intersection(source_key) or source in source_points or
                ('source_key' in row and tuple(row['source_key']) != source_key)):
            raise ValueError('Duplicate, overlapping or stale physical source identity')
        source_keys.add(source_key)
        source_pads.update(source_key)
        source_points.add(source)
        if source == target or list(map(tuple, row['path'])) != [source, target]:
            raise ValueError('Fresh physical source-to-target paths required')
        normalized = dict(row, source=local(source), target=local(target), source_key=list(source_key), width=track_width_nm,
                          path=[local(source), local(target)])
        if 'departure_waypoint_nm' in row:
            normalized['departure_waypoint_nm'] = local(_departure_waypoint(source, row['heading'], row['departure_waypoint_nm']))
        rows.append(normalized)
    board = tuple(local(p) for p in board)
    obstacles = tuple(tuple(local(p) for p in bounds) for bounds in obstacles)
    # Retain the physical source order established by the group, including
    # target permutations. Native admission is responsible for crossings.
    pitch = track_width_nm+clearance_nm+25000
    pitches = [('compact', pitch)]
    bridge_metadata = via_width_nm is not None or via_envelope_radius_nm is not None
    bridge_pitch = pitch
    if via_width_nm is not None:
        bridge_pitch = max(bridge_pitch, (via_width_nm+track_width_nm+1)//2+clearance_nm+25000)
    if via_envelope_radius_nm is not None:
        bridge_pitch = max(bridge_pitch, via_envelope_radius_nm+(track_width_nm+1)//2+25000)
    if bridge_pitch > pitch:
        pitches.append(('bridge', bridge_pitch))
    # Both pitches use each common rail in turn; one wider family's footprint
    # keeps that rail inside the board without forcing every body to be wide.
    radius = math.ceil(((len(rows)-1)*pitches[-1][1]+track_width_nm)/2)+clearance_nm
    results, seen = [], set()
    distinct_owners = len({row['net'] for row in rows}) == len(rows)

    def add(paths, family, spine=None, bodies=None, *, pitch_nm=None, pitch_family=None):
        if time.monotonic() >= deadline or len(results) >= max_candidates:
            return
        if any('departure_waypoint_nm' in row and p[:2] != [row['source'], row['departure_waypoint_nm']]
               for p, row in zip(paths, rows)):
            return
        if any(not _departure_compatible(p, row['heading']) or not valid_shape(p) or
               not paths_clear([p], [track_width_nm], 0) for p, row in zip(paths, rows)):
            return
        if any(not all(board[0][i] <= point[i] <= board[1][i] for i in (0, 1)) for p in paths for point in p):
            return
        paths = [[global_point(p) for p in path] for path in paths]
        key = tuple(tuple(p) for p in paths)
        if key in seen:
            return
        seen.add(key)
        digest = hashlib.sha256(repr((face, key, track_width_nm, clearance_nm)).encode()).hexdigest()[:16]
        nets = [r['net'] for r in rows]
        physical_order = [dict(net=r['net'], source_key=list(r['source_key'])) for r in rows]
        lanes = [dict(net=r['net'], source_key=list(r['source_key']), layer=face, path=p,
                      source=global_point(r['source']), target=global_point(r['target']),
                      target_pad_id=r['target_pad_id']) for r, p in zip(rows, paths)]
        for lane, row in zip(lanes, rows):
            if 'departure_waypoint_nm' in row:
                lane['departure_waypoint_nm'] = global_point(row['departure_waypoint_nm'])
        records = [dict(id=f'corridor-{digest}-{i}', layer=face, nets=[rows[i]['net']], path=list(p),
                        width_nm=track_width_nm+2*clearance_nm) for i, p in enumerate(paths)]
        diagnostics = dict(geometry_only=True, bundle=len(rows)>1, singleton_fallback=len(rows)==1,
                           family=family, cohort_members=list(nets), cohort_sources=physical_order)
        if not distinct_owners:
            diagnostics['inherited_helper_skipped'] = 'repeated_electrical_owner'
        if pitch_nm is not None:
            diagnostics['pitch_nm'] = pitch_nm
        if pitch_family is not None:
            diagnostics['pitch_family'] = pitch_family
        if spine is not None:
            diagnostics.update(spine=[global_point(p) for p in spine],
                               lane_bodies=[[global_point(p) for p in body] for body in bodies])
        results.append(dict(lanes=lanes, physical_order=physical_order, envelope_records=records, diagnostics=diagnostics))

    if len(rows) == 1:
        row = rows[0]
        waypoint = row.get('departure_waypoint_nm')
        for path in octile(waypoint if waypoint is not None else row['source'], row['target']):
            if waypoint is not None:
                attached = _attach(path, row['source'], row['target'], track_width_nm,
                                   heading=row['heading'], departure_waypoint_nm=waypoint)
                if attached is not None:
                    add([attached[0]], 'singleton')
            else:
                add([path], 'singleton')
        if results:
            return results
        # A certified departure can point away from every target. Retain the
        # same analytic rail attachments when monotone singleton paths fail.
    def offer_spine(spine,family):
        for label, lane_pitch in pitches:
            if time.monotonic() >= deadline or len(results) >= max_candidates:
                break
            offsets = ([round((i-(len(rows)-1)/2)*lane_pitch) for i in range(len(rows))]
                       if not bridge_metadata else
                       [i*lane_pitch-round((len(rows)-1)*lane_pitch/2) for i in range(len(rows))])
            try:
                bodies = [offset_path(spine, offset) if offset else list(spine) for offset in offsets]
            except ValueError:
                continue
            attached = [_attach(body, row['source'], row['target'], track_width_nm, heading=row['heading'],
                                departure_waypoint_nm=row.get('departure_waypoint_nm')) for body, row in zip(bodies, rows)]
            if all(attached):
                add([q[0] for q in attached], family, spine, [q[1] for q in attached], pitch_nm=lane_pitch,
                    pitch_family=label if bridge_metadata else None)
    for spine in _rail_spines(rows, board, obstacles, radius):
        if time.monotonic() >= deadline or len(results) >= max_candidates:
            break
        offer_spine(spine,'rail')
    # The inherited helper requires distinct electrical owners. Repeated-net
    # roots retain the analytic rail family without inventing net aliases.
    if len(rows)>1 and distinct_owners and time.monotonic() < deadline and len(results) < max_candidates:
        helper_rows = [dict(row, path=[row.get('departure_waypoint_nm', row['source']), row['target']]) for row in rows]
        families = []
        for label, lane_pitch in pitches:
            if time.monotonic() >= deadline:
                break
            # The inherited helper already applies the same 25k margin.
            candidates = corridor_candidates(helper_rows, clearance_nm=lane_pitch-track_width_nm-25000,
                                             limit=max_candidates, deadline=deadline)
            families.append((label, lane_pitch, candidates))
        # Rank-wise interleaving keeps the optional wider helper family from
        # being hidden behind all compact candidates under the shared cap.
        for batch in zip_longest(*(q[2] for q in families)):
            if time.monotonic() >= deadline or len(results) >= max_candidates:
                break
            for (label, lane_pitch, _), candidate in zip(families, batch):
                if candidate is None or time.monotonic() >= deadline or len(results) >= max_candidates:
                    continue
                options = dict(pitch_nm=lane_pitch, pitch_family=label) if bridge_metadata else {}
                if any('departure_waypoint_nm' in row for row in rows):
                    attached = [_attach(path, row['source'], row['target'], track_width_nm, heading=row['heading'],
                                        departure_waypoint_nm=row.get('departure_waypoint_nm'))
                                if 'departure_waypoint_nm' in row else (path, path)
                                for path, row in zip(candidate['paths'], rows)]
                    if all(attached):
                        add([q[0] for q in attached], 'existing', **options)
                else:
                    add(candidate['paths'], 'existing', **options)
    if not results:
        for spine in _return_spines(rows,board,obstacles,radius,deadline):
            if time.monotonic()>=deadline or len(results)>=max_candidates:break
            offer_spine(spine,'return')
    return results
