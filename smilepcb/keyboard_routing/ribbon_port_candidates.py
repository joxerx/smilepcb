"""Bounded deterministic free-space seeds; Native A* still proves the corridor."""
import math

MAX_FALLBACK_RINGS=64
MAX_DEPARTURE_STEPS=64


def departure_possible(start,heading,faces,*,step,minimum_run,bounds,edge_clear,portal):
    """Necessary prefix filter: False rejects, None defers to full path search.

    Fine lattices may exceed the bounded preflight; deferral must not be
    interpreted as blocked or as a physically verified departure.
    """
    start=tuple(start);heading=tuple(heading);faces=tuple(faces);bounds=tuple(bounds)
    if (type(step)is not int or step<=0 or type(minimum_run)is not int or minimum_run<=0
            or heading not in ((1,0),(-1,0),(0,1),(0,-1)) or len(start)!=2 or not faces
            or len(bounds)!=4 or any(type(v)is not int for v in (*start,*bounds))
            or bounds[0]>=bounds[2] or bounds[1]>=bounds[3]
            or not callable(edge_clear) or not callable(portal)):
        raise ValueError('Invalid departure geometry or callbacks')
    if not all(bounds[i]<=start[i]<=bounds[i+2] for i in (0,1)):return False
    steps=(minimum_run+step-1)//step
    if steps>MAX_DEPARTURE_STEPS:return None
    def portal_inside(point,face):
        proposal=portal(point,face)
        if not isinstance(proposal,dict):return False
        end=proposal.get('end')
        return (isinstance(end,(tuple,list)) and len(end)==2
                and all(type(v)is int for v in end)
                and all(bounds[i]<=end[i]<=bounds[i+2] for i in (0,1)))
    for face in faces:
        point=start
        if portal_inside(point,face):return True
        for _ in range(steps):
            following=(point[0]+heading[0]*step,point[1]+heading[1]*step)
            if not all(bounds[i]<=following[i]<=bounds[i+2] for i in (0,1)):break
            if not edge_clear(point,following,face):break
            point=following
            if portal_inside(point,face):return True
            if (point[0]-start[0])*heading[0]+(point[1]-start[1])*heading[1]>=minimum_run:return True
    return False


def port_pairs(source,targets,heading,bounds,*,step_nm,lead_nm,minimum_run_nm,free,limit=16,departure_free=None,sampling=None):
    """Return distinct integer (start,goal) pairs in deterministic quality order.

    Heading is a cardinal bank direction. Endpoint occupancy and an optional
    necessary departure predicate are sampled;
    no returned pair is a path, connectivity, or physical-width certificate.
    The fallback samples at most 64 transverse levels. An optional sampling
    dict reports sparse coverage; an empty menu never proves no route exists.
    """
    if (type(step_nm)is not int or step_nm<=0 or type(lead_nm)is not int or lead_nm<0
            or type(minimum_run_nm)is not int or minimum_run_nm<=0
            or type(limit)is not int or not 1<=limit<=256
            or not callable(free) or departure_free is not None and not callable(departure_free)
            or sampling is not None and not isinstance(sampling,dict)):raise ValueError('Invalid port limits')
    source=tuple(source);targets=[tuple(q) for q in targets];heading=tuple(heading)
    if (heading not in ((1,0),(-1,0),(0,1),(0,-1)) or len(source)!=2 or len(bounds)!=4
            or any(type(v)is not int for v in (*source,*bounds,*(v for q in targets for v in q)))
            or any(len(q)!=2 for q in targets) or bounds[0]>=bounds[2] or bounds[1]>=bounds[3]):
        raise ValueError('Expected integer geometry and cardinal heading')
    stats=sampling if sampling is not None else {}
    stats.update(primary_pairs=0,fallback_rings=0,sparse_fallback=False,endpoint_queries=0,departure_queries=0,
                 fallback_ring_limit=MAX_FALLBACK_RINGS,returned_pairs=0,selection='no-targets')
    if not targets:return []
    normal=(-heading[1],heading[0]);minimum=max(2000000,minimum_run_nm)
    projection=lambda q:sum((q[i]-source[i])*heading[i] for i in (0,1))
    forward=[projection(q) for q in targets if projection(q)>0]
    nearest=min(forward) if forward else None
    coherent=len(forward)==len(targets)
    # Keep eight local launch positions even when ownership bounds span the
    # whole board; nine sparse positions retain the original far endpoint.
    # Align distances to the global grid *along heading*. Nearest rounding of
    # an off-grid source could otherwise put the first point inside its lead.
    extent=sum(abs(heading[i])*(bounds[i+2]-bounds[i]) for i in (0,1))
    nominal_first=(lead_nm+step_nm-1)//step_nm*step_nm
    maximum=max(nominal_first,extent//4//step_nm*step_nm)
    axis=0 if heading[0] else 1;oriented_source=source[axis]*heading[axis]
    first=(oriented_source+lead_nm+step_nm-1)//step_nm*step_nm-oriented_source
    last=(oriented_source+maximum)//step_nm*step_nm-oriented_source
    intervals=(last-first)//step_nm
    if intervals<0:distances=[]
    elif intervals<17:distances=[first+i*step_nm for i in range(intervals+1)]
    else:
        indices=set(range(8))
        indices.update(7+(i*(intervals-7)+8)//9 for i in range(1,10))
        distances=[first+i*step_nm for i in sorted(indices)]
    snap=lambda q:tuple(round(v/step_nm)*step_nm for v in q)
    mean=snap(tuple(round(sum(q[i] for q in targets)/len(targets)) for i in (0,1)))
    spread=max(abs(sum((q[i]-mean[i])*normal[i] for i in (0,1))) for q in targets)
    release=2*step_nm+min(spread,nearest//4) if coherent else 0
    cap=nearest-release if nearest is not None else None
    # A terminal far to the side can have a small forward projection. That
    # projection is a preference for branch placement, not a physical wall.
    # If it contradicts the required departure and minimum common run, leave
    # the choice to the actual corridor/attachment clearance proofs instead.
    if cap is not None and cap < lead_nm+minimum:cap=None
    if cap is not None and projection(mean)>cap:
        excess=math.ceil((projection(mean)-cap)/step_nm)*step_nm
        mean=tuple(mean[i]-heading[i]*excess for i in (0,1))
    occupancy={}
    def available(q):
        if not all(bounds[i]<=q[i]<=bounds[i+2] for i in (0,1)):return False
        if q not in occupancy:
            stats['endpoint_queries']+=1
            occupancy[q]=bool(free(q))
        return occupancy[q]
    departure_cache={}
    runs=sorted({minimum,2*minimum,4*minimum,4000000,8000000,10000000})
    def collect(tangents):
        found=set()
        for distance in distances:
            for tangent in tangents:
                start=snap(tuple(source[i]+heading[i]*distance+normal[i]*tangent for i in (0,1)))
                if not lead_nm<=projection(start)<=maximum or not available(start):continue
                if departure_free is not None:
                    if start not in departure_cache:
                        stats['departure_queries']+=1
                        status=departure_free(start)
                        departure_cache[start]=status is None or bool(status)
                    if not departure_cache[start]:continue
                goals=[mean] if nearest is not None else []
                if coherent:
                    goals=[tuple(mean[i]-heading[i]*extra+normal[i]*offset for i in (0,1))
                           for extra in (0,2*step_nm,4*step_nm) for offset in (0,-2*step_nm,2*step_nm)]
                goals += [snap(tuple(start[i]+heading[i]*run for i in (0,1))) for run in runs]
                for goal in goals:
                    run=projection(goal)-projection(start)
                    if run<minimum or cap is not None and projection(goal)>cap:continue
                    if nearest is None and run>max(10000000,minimum):continue
                    if available(goal):found.add((start,goal))
        return found
    primary=(0,-2*step_nm,2*step_nm)
    pairs=collect(primary)
    stats['primary_pairs']=len(pairs)
    stats['selection']='primary' if pairs else 'sampled-without-pair'
    if not pairs:
        transverse_extent=max(abs(normal[0]*(x-source[0])+normal[1]*(y-source[1]))
                              for x in (bounds[0],bounds[2]) for y in (bounds[1],bounds[3]))
        max_ring=(transverse_extent+step_nm-1)//step_nm
        # Preserve nearby rings, then sample the whole remaining extent with
        # an integer stride independent of lattice resolution or board size.
        dense=min(max_ring,MAX_FALLBACK_RINGS//2)
        rings=set(range(1,dense+1))
        remaining=MAX_FALLBACK_RINGS-dense
        if max_ring>dense:
            rings.update(dense+(i*(max_ring-dense)+remaining-1)//remaining for i in range(1,remaining+1))
        stats['sparse_fallback']=len(rings)<max_ring
        for ring in sorted(rings):
            tangents=tuple(v for v in (-ring*step_nm,ring*step_nm) if v not in primary)
            if not tangents:continue
            stats['fallback_rings']+=1
            pairs=collect(tangents)
            if pairs:
                stats['selection']='fallback'
                break
    def rank(pair):
        start,goal=pair;run=projection(goal)-projection(start)
        return (sum((start[i]-source[i])**2 for i in (0,1)),-run,
                sum(math.dist(goal,q) for q in targets),start,goal)
    answer=sorted(pairs,key=rank)[:limit]
    stats['returned_pairs']=len(answer)
    return answer
