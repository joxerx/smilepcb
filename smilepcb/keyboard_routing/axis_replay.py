"""Re-prove a supplied track-only axis against the current physical world."""
from .polish_geometry import valid_shape


def replay_track_axis(edges, *, bounds, layers, edge_clear, goal_accept):
    result = dict(accepted=False, edges=edges, axis_clear=False,
                  reason='Invalid supplied track axis', expanded=0, generated=0)
    if not edges:
        return result
    face = edges[0].get('layer')
    if face not in layers:
        return result
    path = []
    for edge in edges:
        a, z = edge.get('start'), edge.get('end')
        if (edge.get('kind') != 'track' or edge.get('layer') != face
                or edge.get('end_layer') != face or a is None or z is None
                or len(a) != 2 or len(z) != 2
                or any(type(v) is not int for v in (*a, *z))
                or (path and tuple(a) != path[-1])):
            return result
        if not path:
            path.append(tuple(a))
        path.append(tuple(z))
    if (not valid_shape(path) or any(not (bounds[0] <= x <= bounds[2]
                                        and bounds[1] <= y <= bounds[3]) for x, y in path)):
        return result
    result['axis_clear'] = all(edge_clear(a, z, face) for a, z in zip(path, path[1:]))
    if not result['axis_clear']:
        result['reason'] = 'Supplied axis blocked by current copper'
        return result
    result['accepted'] = bool(goal_accept(edges))
    result['reason'] = None if result['accepted'] else 'Supplied axis attachments rejected'
    return result


def trim_track_axis_endpoints(edges, *, free, minimum_run_nm=2000000):
    """Return a contiguous existing-vertex subaxis with free endpoint ports.

    No new coordinates or edges are generated. Full swept-edge clearance and
    attachment admission must still be re-proved by ``replay_track_axis``.
    """
    from copy import deepcopy
    import math
    if (not edges or not callable(free) or type(minimum_run_nm) is not int
            or minimum_run_nm<=0 or not isinstance(edges[0],dict)):
        return None
    face=edges[0].get('layer');path=[]
    if type(face) is not int or face<0:return None
    for edge in edges:
        if not isinstance(edge,dict):return None
        a,z=edge.get('start'),edge.get('end')
        if (edge.get('kind')!='track' or edge.get('layer')!=face
                or edge.get('end_layer')!=face or edge.get('payload') is not None
                or not isinstance(a,(tuple,list)) or not isinstance(z,(tuple,list))
                or len(a)!=2 or len(z)!=2 or any(type(v) is not int for v in (*a,*z))
                or path and tuple(a)!=path[-1]):
            return None
        if not path:path.append(tuple(a))
        path.append(tuple(z))
    if not valid_shape(path):return None
    available=[i for i,point in enumerate(path) if free(point,face)]
    if len(available)<2:return None
    first,last=available[0],available[-1]
    if sum(math.dist(a,z) for a,z in zip(path[first:last],path[first+1:last+1]))<minimum_run_nm:
        return None
    return deepcopy(edges[first:last])
