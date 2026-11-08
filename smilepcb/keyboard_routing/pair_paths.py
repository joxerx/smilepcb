"""Bounded coupled octile candidates for aligned two-lane terminals."""


def paired_octile_paths(starts,ends):
    """Slide a shared 45-degree turn without choosing a board-specific corridor.

    Cardinal terminal banks must have the same lane ordering. This is a
    candidate generator only; native collision and pair-length checks decide
    admission. Boards with incompatible banks use the existing routing path.
    """
    a,z=starts;c,d=ends;dx,dy=z[0]-a[0],z[1]-a[1]
    if bool(dx)==bool(dy) or all(s==t for s,t in zip(starts,ends)):return
    v=(0,1 if dy>0 else -1) if dy else (1 if dx>0 else -1,0)
    ex,ey=d[0]-c[0],d[1]-c[1]
    if dx*ey!=dy*ex or dx*ex+dy*ey<=0:return
    u=(v[1],-v[0]);delta=(c[0]+d[0]-a[0]-z[0],c[1]+d[1]-a[1]-z[1])
    if sum(u[i]*delta[i] for i in (0,1))<0:u=tuple(-q for q in u)
    local=lambda p:(sum(p[i]*u[i] for i in (0,1)),sum(p[i]*v[i] for i in (0,1)))
    world=lambda p:(p[0]*u[0]+p[1]*v[0],p[0]*u[1]+p[1]*v[1])
    aa=list(map(local,starts));bb=list(map(local,ends))
    low=max(p[0] for p in aa);high=min(t[0]-abs(t[1]-s[1]) for s,t in zip(aa,bb))
    if high<low:return
    step=max(125000,(high-low+254)//255)
    for bend in sorted(set(range(low,high+1,step))|{high}):
        paths=[]
        for start,end in zip(aa,bb):
            points=[start,(bend,start[1]),(bend+abs(end[1]-start[1]),end[1]),end]
            paths.append([world(q) for i,q in enumerate(points) if not i or q!=points[i-1]])
        yield paths


def paired_spine_paths(starts,ends,spine,*,source_heading,arrival_heading,
                       target_lead_nm,min_body_segment_nm=0):
    """One guided true-offset pair with exact protected heads and pad arrivals.

    The caller chooses the common spine; this function does not select a board
    corridor. Source heads may be at different stations on the first parallel
    carriers, including BEFORE the supplied spine start. Offsets are derived
    from their exact normal projections, never the cut-bank Euclidean span.
    A cardinal final carrier ends in a short octile fanout with the requested
    arrival heading. Returns two paths or None for unsupported geometry.
    Native clearance, contact, skew/length policy and ownership remain caller
    obligations; this is neither an admission nor an SI qualification.
    """
    import math
    from .corridor_geometry import offset_path
    from .follow_geometry import direction,simplify
    from .polish_geometry import octile,valid_shape
    if type(target_lead_nm) is not int or target_lead_nm<=0 or type(min_body_segment_nm) is not int or min_body_segment_nm<0:
        raise ValueError('Positive terminal lead and nonnegative body minimum required')
    def point(value):
        if not isinstance(value,(list,tuple)) or len(value)!=2 or any(type(v) is not int for v in value):
            raise ValueError('Integer nm geometry required')
        return tuple(value)
    if len(starts)!=2 or len(ends)!=2:
        raise ValueError('Exactly two source heads and target pads required')
    starts=list(map(point,starts));ends=list(map(point,ends));spine=simplify(map(point,spine))
    source_heading,arrival_heading=point(source_heading),point(arrival_heading)
    if (max(map(abs,source_heading))!=1 or sum(v*v for v in arrival_heading)!=1 or
            not valid_shape(spine) or direction(spine[0],spine[1])!=source_heading or
            direction(spine[-2],spine[-1])!=arrival_heading):return None
    normal=(-source_heading[1],source_heading[0]);norm=math.hypot(*normal)
    offsets=[sum(normal[i]*(start[i]-spine[0][i]) for i in (0,1))/norm for start in starts]
    arrival_normal=(-arrival_heading[1],arrival_heading[0])
    end_order=sum(arrival_normal[i]*(ends[1][i]-ends[0][i]) for i in (0,1))
    if (offsets[1]-offsets[0])*end_order<=0:return None
    station=lambda q:sum(q[i]*arrival_heading[i] for i in (0,1))
    fan_station=min(map(station,ends))-target_lead_nm
    result=[]
    for source,target,offset in zip(starts,ends,offsets):
        try:lane=offset_path(spine,offset) if offset else list(spine)
        except ValueError:return None
        delta=tuple(source[i]-lane[0][i] for i in (0,1))
        # Exact normal derivation makes this collinear even for diagonal
        # integer-grid offsets. Replace/extend that endpoint, never insert a
        # microscopic connector from an approximately rounded pitch.
        if (delta[0]*source_heading[1]!=delta[1]*source_heading[0] or
                sum((lane[1][i]-source[i])*source_heading[i] for i in (0,1))<=0):return None
        if fan_station<station(lane[-2]):return None
        shift=fan_station-station(lane[-1])
        fan=tuple(lane[-1][i]+arrival_heading[i]*shift for i in (0,1))
        body=simplify([source,*lane[1:-1],fan])
        if any(math.dist(a,b)<min_body_segment_nm for a,b in zip(body,body[1:])):return None
        choices=[]
        for tail in octile(fan,target):
            path=simplify([*body,*tail[1:]])
            if (valid_shape(path) and direction(path[0],path[1])==source_heading and
                    direction(path[-2],path[-1])==arrival_heading and
                    all(math.dist(a,b)>1 for a,b in zip(path,path[1:]))):choices.append(path)
        if not choices:return None
        result.append(min(choices,key=lambda path:(len(path),sum(math.dist(a,b) for a,b in zip(path,path[1:])),path)))
    return result
