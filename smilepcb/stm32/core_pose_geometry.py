"""Bounded directional core-pose estimates. No Native feasibility is claimed."""
from collections import Counter
from copy import deepcopy
import json
import math
import time


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2 or any(type(x) is not int for x in value):
        raise ValueError('Integer nm point required')
    return tuple(value)


def _pose(value):
    origin = _point(value['origin_nm'])
    angle = value['rotation_deg']
    if type(angle) is not int or angle not in (0, 90, 180, 270):
        raise ValueError('Cardinal core rotation required')
    key = value.get('pose_key', f'{origin[0]}:{origin[1]}:{angle}')
    if not isinstance(key, str) or not key:
        raise ValueError('Stable string pose_key required')
    return dict(pose_key=key, origin_nm=list(origin), rotation_deg=angle)


def _turn(point, angle):
    x, y = point
    return ((x, y), (-y, x), (-x, -y), (y, -x))[angle//90]


def _bank(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 3 or not isinstance(value[0], str) or
            type(value[1]) is not int or value[1] not in (0, 1) or
            type(value[2]) is not int or value[2] not in (-1, 1)):
        raise ValueError('Physical local bank identity required')
    return tuple(value)


def _positive(value):
    if type(value) is not int or value <= 0:
        raise ValueError('Positive integer process dimension required')
    return value


def shortlist_core_poses(candidates, roots_local, target_sets, channel_windows, critical_support_domains,
                         *, process, preferred_nm, baseline, limit=6):
    """Keep baseline first, then rank supplied boundary-event/cardinal poses.

    Windows are physical free tangent intervals at sampled depths. Capacities
    sum across disjoint intervals and take the bottleneck across depths. The
    adapter supplies actual components, pads and support domains; this routine
    never invents target centroids, source net aliases or a dense search grid.
    """
    if type(limit) is not int or not 1 <= limit <= 6:
        raise ValueError('Geometry shortlist cap must be 1..6 including baseline')
    preferred = _point(preferred_nm)
    width, gap, via = (_positive(process[k]) for k in ('track_width_nm', 'clearance_nm', 'via_width_nm'))
    radius = _positive(process['via_envelope_radius_nm']) if 'via_envelope_radius_nm' in process else 0
    half = (width+1)//2
    margin = max(half+gap, (via+1)//2+gap, radius)
    pitch = max(width+gap, (via+width+1)//2+gap, radius+half)
    roots, occupied = [], set()
    for root in roots_local:
        key = root['source_key']
        if (not isinstance(key, (list, tuple)) or not key or any(not isinstance(k, str) or not k for k in key) or
                list(key) != sorted(set(key)) or occupied.intersection(key)):
            raise ValueError('Disjoint canonical physical source keys required')
        occupied.update(key)
        departures = []
        for d in root['departures']:
            point, heading = _point(d['point_nm']), _point(d['heading'])
            if max(map(abs, heading)) != 1 or d['layer'] not in (0, 2) or type(d['layer']) is not int:
                raise ValueError('Octile heading and actual copper layer required')
            departures.append(dict(point=point, heading=heading, layer=d['layer'], bank=_bank(d['bank'])))
        if not departures:
            raise ValueError('Each physical root needs departure options')
        targets = []
        for t in target_sets.get(root['net'], []):
            point = _point(t['point'])
            if not t.get('pad_id') or not t['layers'] or any(type(f) is not int or f not in (0, 2) for f in t['layers']):
                raise ValueError('Actual numbered target pad and layers required')
            targets.append(dict(point=point, layers=list(t['layers']), pad_id=t['pad_id']))
        roots.append(dict(source_key=list(key), departures=departures, targets=targets))
    if not roots:
        raise ValueError('Actual physical source roots required')
    poses, keys, positions = [], set(), set()
    for raw in candidates:
        p = _pose(raw); geometry = (*p['origin_nm'], p['rotation_deg'])
        if p['pose_key'] in keys or geometry in positions:
            raise ValueError('Unique candidate pose identities required')
        keys.add(p['pose_key']); positions.add(geometry); poses.append(p)
    base = _pose(baseline)
    match = next((p for p in poses if p['origin_nm'] == base['origin_nm'] and p['rotation_deg'] == base['rotation_deg']), None)
    if match is not None:
        base = match
    elif base['pose_key'] in keys:
        raise ValueError('Baseline pose_key refers to another geometry')
    else:
        poses.append(base)

    def estimate(p):
        slices = {}
        for window in channel_windows.get(p['pose_key'], []):
            bank, face, depth = _bank(window['bank']), window['layer'], window['depth_nm']
            if type(face) is not int or face not in (0, 2) or type(depth) is not int or depth < 0:
                raise ValueError('Actual layer and integer sampling depth required')
            identity = (bank, face, depth)
            if identity in slices:
                raise ValueError('One complete interval inventory per depth required')
            capacity, previous = 0, None
            for interval in sorted(map(_point, window['intervals_nm'])):
                lo, hi = interval
                if lo >= hi or previous is not None and lo < previous:
                    raise ValueError('Disjoint positive physical channel intervals required')
                previous = hi
                if hi-lo >= 2*margin:
                    capacity += 1+(hi-lo-2*margin)//pitch
            slices[identity] = capacity
        capacities = {}
        for (bank, face, _), capacity in slices.items():
            key = bank, face
            capacities[key] = min(capacities.get(key, capacity), capacity)
        demands, adverse = Counter(), 0
        for root in roots:
            choices = []
            for d in root['departures']:
                local, heading = _turn(d['point'], p['rotation_deg']), _turn(d['heading'], p['rotation_deg'])
                source = tuple(local[i]+p['origin_nm'][i] for i in (0, 1))
                direction = 2
                for target in root['targets']:
                    vector = tuple(target['point'][i]-source[i] for i in (0, 1))
                    dot = sum(heading[i]*vector[i] for i in (0, 1))
                    forward = dot > 0 and 2*dot*dot >= sum(x*x for x in heading)*sum(x*x for x in vector)
                    direction = min(direction, int(not forward)+int(d['layer'] not in target['layers']))
                identity = d['bank'], d['layer']
                choices.append((direction, -capacities.get(identity, 0), identity))
            cost, _, identity = min(choices)
            demands[identity] += 1
            adverse += cost
        banks = [dict(bank=list(bank), layer=face, demand=n, capacity=capacities.get((bank, face), 0),
                      deficit=max(0, n-capacities.get((bank, face), 0))) for (bank, face), n in sorted(demands.items())]
        domains = critical_support_domains.get(p['pose_key'], [])
        if any(not isinstance(d.get('domains'), list) for d in domains):
            raise ValueError('Support templates require explicit available domain lists')
        counts = [len(d['domains']) for d in domains]
        empty = sum(n == 0 for n in counts)
        worst = max(b['deficit'] for b in banks)
        total = sum(b['deficit'] for b in banks)
        distance = sum((p['origin_nm'][i]-preferred[i])**2 for i in (0, 1))
        score = [worst, total, empty, -min(counts, default=0), adverse, distance]
        return dict(p, diagnostics=dict(estimated=True, root_count=len(roots), banks=banks,
                    worst_bank_deficit=worst, total_deficit=total, adverse_departures=adverse,
                    support_empty_domains=empty, score=score, lane_pitch_nm=pitch))

    evaluated = [estimate(p) for p in poses]
    answer = [next(p for p in evaluated if p['pose_key'] == base['pose_key'])]
    remaining = sorted((p for p in evaluated if p['pose_key'] != base['pose_key']),
                       key=lambda p: (p['diagnostics']['score'], p['rotation_deg'], p['origin_nm']))
    while remaining and len(answer) < limit:
        pool = remaining
        if len(answer) > 1:
            rotations = {p['rotation_deg'] for p in answer}
            origins = {tuple(p['origin_nm']) for p in answer}
            diverse = [p for p in remaining if p['rotation_deg'] not in rotations and tuple(p['origin_nm']) not in origins]
            pool = diverse or [p for p in remaining if p['rotation_deg'] not in rotations] or pool
        chosen = pool[0]
        answer.append(chosen); remaining.remove(chosen)
    return deepcopy(answer)


def _balanced_metrics(value):
    spaces = value['side_space_nm']
    if (not isinstance(spaces, dict) or set(spaces) != {'left', 'right', 'top', 'bottom'} or
            any(type(v) is not int or v < 0 for v in spaces.values())):
        raise ValueError('Four nonnegative integer side spaces required')
    headroom, adverse = value['bank_headroom_nm'], value.get('adverse_departures', 0)
    if type(headroom) is not int or type(adverse) is not int or adverse < 0:
        raise ValueError('Signed integer bank headroom and nonnegative target cost required')
    return (*(-v for v in sorted(spaces.values())), -headroom, adverse)


def balanced_pose_search(bounds_nm, baseline, *, coarse_step_nm, min_step_nm, oracle,
                         deadline, max_evaluations, limit=6):
    """Bounded whole-board XY/cardinal sampling, then diverse-basin refinement.

    The oracle measures the entire rigid core. Coverage refers ONLY to the
    requested coarse lattice, never all continuous positions or a global proof.
    When that lattice exceeds the budget, globally stratified samples precede
    refinement and coverage_complete is false. No near-baseline-only fallback.
    """
    if len(bounds_nm) == 4:
        if any(type(v) is not int for v in bounds_nm):
            raise ValueError('Integer board bounds required')
        x0, y0, x1, y1 = bounds_nm
    else:
        (x0, y0), (x1, y1) = map(_point, bounds_nm)
    if x0 >= x1 or y0 >= y1:
        raise ValueError('Positive fixed board domain required')
    for value in (coarse_step_nm, min_step_nm, max_evaluations):
        _positive(value)
    if coarse_step_nm < min_step_nm or type(limit) is not int or not 1 <= limit <= 6:
        raise ValueError('Coarse >= minimum resolution and shortlist cap 1..6 required')
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError('Finite original search deadline required')
    base = _pose(baseline)
    origin = tuple(base['origin_nm'])
    samples, cache, calls = [], {}, 0
    started = time.monotonic()
    stop = None
    coarse_stop = None
    coarse_deadline=started+max(0,deadline-started)*(.5 if coarse_step_nm>min_step_nm else 1.)
    duplicate_hits = 0

    def query(p, phase, step):
        nonlocal calls, stop, coarse_stop, duplicate_hits
        identity = (*p['origin_nm'], p['rotation_deg'])
        if identity in cache and not (phase=='refine' and cache[identity]['status']=='coarse_expired'):
            duplicate_hits += 1
            if phase == 'coarse':
                cache[identity]['coarse_sample'] = True
            return cache[identity]
        if time.monotonic() >= deadline:
            stop = 'deadline'; return None
        if phase=='coarse' and time.monotonic()>=coarse_deadline:
            coarse_stop='time_reserve';return None
        if calls >= max_evaluations:
            stop = 'evaluation_budget'; return None
        row = dict(pose=deepcopy(p), phase=phase, step_nm=step, legal=False)
        x, y = p['origin_nm']
        if not x0 <= x <= x1 or not y0 <= y <= y1:
            row['status'] = 'outside_bounds'
        else:
            calls += 1
            try:
                value = oracle(deepcopy(p), deadline=coarse_deadline if phase=='coarse' else deadline)
                json.dumps(value, allow_nan=False)
                if not isinstance(value, dict) or type(value.get('legal')) is not bool:
                    raise ValueError('Oracle legal must be a literal boolean')
                for key in ('side_space_nm', 'bank_headroom_nm', 'adverse_departures'):
                    if key in value:
                        row[key] = deepcopy(value[key])
                row.update(legal=value['legal'], status='legal' if value['legal'] else 'illegal')
                if value['legal']:
                    row['score'] = [*_balanced_metrics(value), sum((p['origin_nm'][i]-origin[i])**2 for i in (0, 1))]
                if time.monotonic() >= deadline:
                    row.update(legal=False, status='expired'); row.pop('score', None); stop = 'deadline'
                elif phase=='coarse' and time.monotonic()>=coarse_deadline:
                    row.update(legal=False,status='coarse_expired');row.pop('score',None);coarse_stop='time_reserve'
            except Exception as error:
                row.update(legal=False, status='oracle_error', reason=type(error).__name__+': '+str(error)[:240])
                row.pop('score', None)
        if phase=='coarse' and coarse_deadline<=time.monotonic()<deadline:
            row.update(legal=False,status='coarse_expired');row.pop('score',None);coarse_stop='time_reserve'
        cache[identity] = row; samples.append(row)
        return row

    def order(row):
        return (row['score'], row['pose']['rotation_deg'], row['pose']['origin_nm'])

    def cell(row):
        x, y = row['pose']['origin_nm']
        return (x-x0)//coarse_step_nm, (y-y0)//coarse_step_nm

    def seeds():
        # Keep each sampled coarse component before repeat XY-cell seeds.
        # These coarse labels are not continuous free-space certificates.
        legal = sorted((q for q in samples if q['legal']), key=order)
        representatives = {}
        for row in legal:
            representatives.setdefault((row.get('coarse_component'), cell(row)), row)
        remaining = list(representatives.values())
        component_leaders = {}
        for row in remaining:
            component_leaders.setdefault(row.get('coarse_component'), row)
        answer = list(component_leaders.values())[:limit]
        remaining = [row for row in remaining if row not in answer]
        while remaining and len(answer) < limit:
            if not answer:
                chosen = remaining[0]
            else:
                def far_key(row):
                    x, y = row['pose']['origin_nm']
                    distance = min((x-q['pose']['origin_nm'][0])**2+(y-q['pose']['origin_nm'][1])**2 for q in answer)
                    return (-distance, order(row))
                chosen = min(remaining, key=far_key)
            answer.append(chosen); remaining.remove(chosen)
        return answer

    baseline_sample = query(base, 'baseline', coarse_step_nm)
    nx = (x1-x0+coarse_step_nm-1)//coarse_step_nm+1
    ny = (y1-y0+coarse_step_nm-1)//coarse_step_nm+1
    planned = nx*ny*4
    available = max(0, max_evaluations-calls)
    bx, by = origin
    baseline_on_lattice = (baseline_sample is not None and x0 <= bx <= x1 and y0 <= by <= y1 and
                           ((bx-x0) % coarse_step_nm == 0 or bx == x1) and
                           ((by-y0) % coarse_step_nm == 0 or by == y1))
    available_positions = available+int(baseline_on_lattice)
    coarse_budget = available_positions if planned <= available_positions else max(0, available//2)
    site_count = min(nx*ny, max(1, coarse_budget//4)) if coarse_budget else 0
    indices = (range(nx*ny) if site_count == nx*ny else
               ((i*(nx*ny-1))//(site_count-1) for i in range(site_count)) if site_count > 1 else
               ((nx*ny-1)//2,) if site_count else ())
    # Farthest-first in actual XY space makes every interrupted prefix
    # spatially diverse. Flattened-index bisection can alias board columns.
    def spatial_sites(values):
        remaining={index:((min(x1,x0+(index%nx)*coarse_step_nm),
                           min(y1,y0+(index//nx)*coarse_step_nm)),float('inf')) for index in values}
        selected=min(remaining) if remaining else None
        while remaining:
            point,_=remaining.pop(selected)
            yield selected
            for key,(q,nearest) in list(remaining.items()):
                d=(q[0]-point[0])**2+(q[1]-point[1])**2
                remaining[key]=(q,min(nearest,d))
            if remaining:selected=max(remaining,key=lambda key:(remaining[key][1],-remaining[key][0][0],-remaining[key][0][1]))
    indices=spatial_sites(indices)
    coarse_done, coarse_measured = 0, 0
    coarse_legal = {}
    for index in indices:
        x, y = min(x1, x0+(index % nx)*coarse_step_nm), min(y1, y0+(index//nx)*coarse_step_nm)
        for angle in (0, 90, 180, 270):
            row = query(_pose(dict(origin_nm=[x, y], rotation_deg=angle)), 'coarse', coarse_step_nm)
            if row is None:
                break
            coarse_done += 1
            coarse_measured += int(row['status'] in ('legal', 'illegal'))
            if row['legal']:
                coarse_legal.setdefault((index % nx, index//nx), []).append(row)
        if stop is not None or coarse_stop is not None:
            break
    if coarse_stop is None:coarse_stop='sampling_complete' if coarse_measured==planned else 'evaluation_allocation'
    coverage = coarse_measured == planned
    # Four-neighbour XY connectivity, unioning all legal orientations per site.
    unseen = set(coarse_legal)
    component_count = 0
    while unseen:
        first = min(unseen); unseen.remove(first); stack = [first]
        while stack:
            ix, iy = stack.pop()
            for row in coarse_legal[ix, iy]:
                row['coarse_component'] = component_count
            for neighbour in ((ix-1,iy),(ix+1,iy),(ix,iy-1),(ix,iy+1)):
                if neighbour in unseen:
                    unseen.remove(neighbour); stack.append(neighbour)
        component_count += 1
    if baseline_sample is not None and baseline_sample['legal'] and 'coarse_component' not in baseline_sample and coarse_legal:
        nearest = min((row for rows in coarse_legal.values() for row in rows),
                      key=lambda row: (sum((row['pose']['origin_nm'][i]-origin[i])**2 for i in (0,1)), order(row)))
        if sum((nearest['pose']['origin_nm'][i]-origin[i])**2 for i in (0,1)) <= 2*coarse_step_nm**2:
            baseline_sample['coarse_component'] = nearest['coarse_component']
    frontier = seeds()
    step, refinement_steps = coarse_step_nm, []
    reached = coverage and step == min_step_nm
    while frontier and step > min_step_nm and stop is None:
        step = max(min_step_nm, step//2)
        refinement_steps.append(step)
        refined, complete = [], True
        for seed in frontier:
            options = [seed]
            sx, sy = seed['pose']['origin_nm']
            for dx in (-step, 0, step):
                for dy in (-step, 0, step):
                    x, y = max(x0, min(x1, sx+dx)), max(y0, min(y1, sy+dy))
                    for angle in (0, 90, 180, 270):
                        row = query(_pose(dict(origin_nm=[x, y], rotation_deg=angle)), 'refine', step)
                        if row is None:
                            complete = False; break
                        if row['legal']:
                            options.append(row)
                    if not complete: break
                if not complete: break
            chosen = min(options, key=order)
            if 'coarse_component' not in chosen:
                chosen = dict(chosen, coarse_component=seed.get('coarse_component'))
            refined.append(chosen)
            if not complete: break
        # Retain untouched basins as well if this level exhausted its budget.
        frontier = refined+frontier[len(refined):]
        reached = complete and step == min_step_nm
        if not complete: break
    base_identity = (*base['origin_nm'], base['rotation_deg'])
    ranked = sorted((q for key, q in {(*q['pose']['origin_nm'],q['pose']['rotation_deg']):q for q in frontier}.items()
                     if key != base_identity), key=order)
    component_leaders = {}
    for row in ranked:
        component_leaders.setdefault(row.get('coarse_component'), row)
    leaders = list(component_leaders.values())
    ranked = leaders+[row for row in ranked if row not in leaders]
    selected = [dict(base, diagnostics=deepcopy({k:v for k,v in (baseline_sample or dict(legal=False,status='unmeasured')).items() if k not in ('pose','phase','step_nm')}))]
    for row in ranked:
        if (*row['pose']['origin_nm'],row['pose']['rotation_deg']) == base_identity:
            continue
        if len(selected) >= limit: break
        selected.append(dict(row['pose'],diagnostics=deepcopy({k:v for k,v in row.items() if k not in ('pose','phase','step_nm')})))
    if stop is None:
        stop = 'min_step_reached' if reached else 'no_legal_samples' if not frontier else 'sampling_complete'
    return dict(poses=selected, samples=samples, stats=dict(bounds_nm=[x0,y0,x1,y1],
        requested_coarse_step_nm=coarse_step_nm, min_step_nm=min_step_nm, max_evaluations=max_evaluations,
        evaluations=calls, coarse_stop_reason=coarse_stop, coarse_deadline_s=coarse_deadline, coarse_planned=planned, coarse_evaluated=coarse_done, coarse_measured=coarse_measured, coverage_complete=coverage,
        coverage_scope='configured coarse lattice only; not continuous domain',
        truncated=not coverage or not reached, stop_reason=stop, min_step_reached=reached,
        refinement_steps_nm=refinement_steps, duplicate_cache_hits=duplicate_hits,
        coarse_components=component_count,
        coarse_component_scope='4-neighbour sampled XY adjacency; union legal orientations; not continuous free-space proof',
        global_optimum_proved=False, elapsed_s=time.monotonic()-started))


def expanded_pose_box(bounds,baseline,pose,*,margin_nm):
    """Transform an actual core bbox about its origin and include escape depth."""
    if (not isinstance(bounds,(tuple,list)) or len(bounds)!=4 or any(type(v) is not int for v in bounds)
            or bounds[0]>=bounds[2] or bounds[1]>=bounds[3] or type(margin_nm) is not int or margin_nm<0):
        raise ValueError('Physical core bounds and nonnegative escape depth required')
    old=_pose(baseline);new=_pose(pose);angle=(new['rotation_deg']-old['rotation_deg'])%360
    points=[_turn((x-old['origin_nm'][0],y-old['origin_nm'][1]),angle)
        for x in (bounds[0],bounds[2]) for y in (bounds[1],bounds[3])]
    return [min(q[0] for q in points)+new['origin_nm'][0]-margin_nm,
        min(q[1] for q in points)+new['origin_nm'][1]-margin_nm,
        max(q[0] for q in points)+new['origin_nm'][0]+margin_nm,
        max(q[1] for q in points)+new['origin_nm'][1]+margin_nm]


def boxes_overlap(a,b):
    return max(a[0],b[0])<=min(a[2],b[2]) and max(a[1],b[1])<=min(a[3],b[3])
