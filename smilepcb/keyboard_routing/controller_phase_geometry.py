"""Finite obstacle-derived via/grid phases; Native must admit every proposal."""


def _point(value):
    if not isinstance(value,(list,tuple)) or len(value)!=2 or any(type(v) is not int for v in value):raise ValueError('Integer nm source point required')
    return tuple(value)


def _box(value):
    if not isinstance(value,(list,tuple)) or len(value)!=4 or any(type(v) is not int for v in value) or value[0]>value[2] or value[1]>value[3]:raise ValueError('Ordered integer nm bounds required')
    return tuple(value)


def _dimension(value,*,positive=False):
    if type(value) is not int or value<0 or positive and value==0:raise ValueError('Integer physical dimension required')
    return value


def iter_via_phase_candidates(obstacles,starts,*,via_width_nm,grid_step_nm,bounds_nm,inset_nm=1000,admissible=None):
    """Lazily admit nearest integer XY representative of each new phase.

    Events use actual annulus radius, each obstacle's clearance and inset.
    No finite-event cap, obstacle admission, route generation or net semantics
    is introduced here. Equal-distance representatives use XY lexical order;
    translations preserve representatives and cardinal turns preserve phases.
    Pure XY events are sorted before any admission. A phase is recorded only
    after its predicate accepts; yield immediately and propagate exceptions.
    """
    if admissible is not None and not callable(admissible):raise ValueError('Phase admission predicate must be callable')
    radius=(_dimension(via_width_nm,positive=True)+1)//2
    step=_dimension(grid_step_nm,positive=True);inset=_dimension(inset_nm)
    bounds=_box(bounds_nm);origins=tuple(_point(p) for p in starts)
    if not origins:raise ValueError('At least one physical source point required')
    xs={p[0] for p in origins};ys={p[1] for p in origins}
    for obstacle in obstacles:
        if not isinstance(obstacle,dict) or 'box_nm' not in obstacle or 'clearance_nm' not in obstacle:raise ValueError('Actual obstacle bounds and clearance required')
        left,top,right,bottom=_box(obstacle['box_nm'])
        margin=radius+_dimension(obstacle['clearance_nm'])+inset
        xs.update(edge+sign*margin for edge in (left,right) for sign in (-1,1))
        ys.update(edge+sign*margin for edge in (top,bottom) for sign in (-1,1))
    events=[];origin=origins[0]
    for x in xs:
        if not bounds[0]<=x<=bounds[2]:continue
        for y in ys:
            if not bounds[1]<=y<=bounds[3]:continue
            phase=((x-origin[0])%step,(y-origin[1])%step)
            if phase==(0,0):continue
            events.append((min((x-a)**2+(y-b)**2 for a,b in origins),(x,y),phase))
    admitted=set()
    for _,point,phase in sorted(events):
        if phase in admitted:continue
        if admissible is not None and not admissible(point):continue
        admitted.add(phase)
        yield point


def via_phase_candidates(obstacles,starts,*,via_width_nm,grid_step_nm,bounds_nm,inset_nm=1000,admissible=None):
    """Compatibility list wrapper; Native adapters should consume the iterator."""
    return list(iter_via_phase_candidates(obstacles,starts,via_width_nm=via_width_nm,grid_step_nm=grid_step_nm,bounds_nm=bounds_nm,inset_nm=inset_nm,admissible=admissible))
