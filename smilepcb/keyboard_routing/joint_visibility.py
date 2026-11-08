"""Bounded exact-geometry visibility portals, independent of native routing."""
import itertools
import math


_SECTORS = (0, 4, 2, 6, 1, 5, 3, 7)


def _interleaved(batches):
    for row in itertools.zip_longest(*batches):
        for value in row:
            if value is not None:
                yield value


def visibility_points(contacts, obstacles, *, bounds, width, via_width,
                      max_points=768, diagnostic=None):
    """Keep mandatory contacts/medials, local sectors, then projection hints.

    All coordinates are integer nanometers. Obstacle circles/bounds already
    carry their effective guards. These points are proposals only; the native
    collision and source-policy oracle must validate every edge and via.
    """
    if type(max_points) is not int or max_points <= 0:
        raise ValueError('Positive visibility point cap required')
    left, top, right, bottom = bounds
    inside = lambda q: left <= q[0] <= right and top <= q[1] <= bottom
    contacts = sorted(set(map(tuple, contacts)))
    if any(not inside(q) for q in contacts):
        raise ValueError('Mandatory terminal outside visibility bounds')
    mandatory = set(contacts)
    circles = []
    local_batches = []
    projection_batches = []
    extras = sorted({width / 2, via_width / 2}, reverse=True)

    def nearest(q):
        return min((math.dist(q, c) for c in contacts), default=0)

    for obstacle in obstacles:
        clearance = obstacle['clearance']
        sectors = {}
        projections = set()

        def local(point, sector, angular_error, extra):
            if inside(point):
                rank = (angular_error, -extra, nearest(point), point)
                values = sectors.setdefault(sector, {})
                if point not in values or rank < values[point]:
                    values[point] = rank

        if 'center' in obstacle:
            x, y = obstacle['center']
            radius = obstacle['radius']
            circles.append((x, y, radius + clearance + width / 2))
            box = (x - radius, y - radius, x + radius, y + radius)
            for extra in extras:
                distance = radius + clearance + extra + 100
                for i in range(16):
                    point = (round(x + distance * math.cos(i * math.pi / 8)),
                             round(y + distance * math.sin(i * math.pi / 8)))
                    local(point, ((i + 1) // 2) % 8, i % 2, extra)
        else:
            box = obstacle['bounds']
        for extra in extras:
            margin = math.ceil(clearance + extra) + 100
            x0, y0 = box[0] - margin, box[1] - margin
            x1, y1 = box[2] + margin, box[3] + margin
            # Corners remain separate quadrants even on a long thin box.
            for point, sector in (((x1, y1), 1), ((x0, y1), 3),
                                  ((x0, y0), 5), ((x1, y0), 7)):
                local(point, sector, 0, extra)
            for x, y in contacts:
                projections.update(((x, y0), (x, y1), (x0, y), (x1, y)))
        by_sector = [[q for q, rank in sorted(sectors.get(sector, {}).items(),
                                             key=lambda row: row[1])]
                     for sector in _SECTORS]
        local_batches.append(list(_interleaved(by_sector)))
        projection_batches.append(sorted((q for q in projections if inside(q)),
                                         key=lambda q: (nearest(q), q)))

    # A legal interval can be narrower than a practical raster step. Keep its
    # actual circle-boundary medial point before any optional portal sampling.
    for i, (x, y, radius) in enumerate(circles):
        for xx, yy, other_radius in circles[i + 1:]:
            distance = math.hypot(xx - x, yy - y)
            if not 0 < distance - radius - other_radius <= max(width, via_width):
                continue
            along = (distance + radius - other_radius) / 2
            point = (round(x + (xx - x) * along / distance),
                     round(y + (yy - y) * along / distance))
            if inside(point):
                mandatory.add(point)
    if len(mandatory) > max_points:
        raise ValueError('Visibility cap cannot retain every mandatory point')

    medial_projections = set()
    for x, y in sorted(mandatory - set(contacts)):
        for a, z in contacts:
            medial_projections.update(((a, y), (x, z),
                                      (a, y + abs(a - x)), (a, y - abs(a - x))))
    medial_projections = {q for q in medial_projections if inside(q)}
    chosen = set(mandatory)
    for point in _interleaved(local_batches):
        if len(chosen) >= max_points:
            break
        chosen.add(point)
    local_selected = len(chosen) - len(mandatory)
    for point in itertools.chain(sorted(medial_projections, key=lambda q: (nearest(q), q)),
                                 _interleaved(projection_batches)):
        if len(chosen) >= max_points:
            break
        chosen.add(point)
    result = sorted(chosen)
    if diagnostic is not None:
        local_candidates = set(itertools.chain.from_iterable(local_batches))
        projection_candidates = set(itertools.chain.from_iterable(projection_batches)) | medial_projections
        diagnostic.update(point_cap=max_points, mandatory_points=len(mandatory),
                          local_candidates=len(local_candidates), local_selected=local_selected,
                          projection_candidates=len(projection_candidates),
                          projection_selected=len(chosen) - len(mandatory) - local_selected,
                          candidate_points=len(mandatory | local_candidates | projection_candidates),
                          selected_points_nm=[list(q) for q in result])
    return result
