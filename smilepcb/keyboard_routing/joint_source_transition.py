"""Bounded ordered source transitions, independent of Native and board identity.

Coordinates, widths and clearances are integer native nanometres. Proposals are
geometry only: the caller must admit *every* lane against current physical copper.
"""
import math
import time
from copy import deepcopy
from .bank_geometry import escape_path

CARDINAL=((1,0),(-1,0),(0,1),(0,-1))


def _inventory(lanes):
    if not lanes or len({q['net'] for q in lanes})!=len(lanes):
        raise ValueError('Distinct source nets required')
    headings={tuple(q['heading']) for q in lanes}
    if len(headings)!=1 or next(iter(headings)) not in CARDINAL:
        raise ValueError('Joint source requires one cardinal physical heading')
    widths={q['width'] for q in lanes}
    if len(widths)!=1 or any(type(w) is not int or w<=0 for w in widths):
        raise ValueError('Joint source requires uniform positive native widths')
    if len({q['target_layer'] for q in lanes})!=1:
        raise ValueError('Joint source requires one body entry face')
    h=next(iter(headings));t=(-h[1],h[0])
    project=lambda point,d:sum(point[i]*d[i] for i in (0,1))
    for q in lanes:
        for key in ('source','target'):
            if len(q[key])!=2 or any(type(v) is not int for v in q[key]):
                raise ValueError('Integer native source/body coordinates required')
    order=sorted(range(len(lanes)),key=lambda i:project(lanes[i]['source'],t))
    sources=[project(lanes[i]['source'],t) for i in order]
    targets=[project(lanes[i]['target'],t) for i in order]
    if any(a>=b for points in (sources,targets) for a,b in zip(points,points[1:])):
        raise ValueError('Unsupported source/body transverse order permutation')
    rail={project(q['target'],h) for q in lanes}
    if len(rail)!=1:raise ValueError('Body entries must share one physical rail')
    end=next(iter(rail))
    if any(project(q['source'],h)>=end for q in lanes):
        raise ValueError('Body entry must be forward of every physical source')
    return h,t,order,end,next(iter(widths))


def _ordered(routes,lanes,h,t,escape_sign):
    """Prove order separately on source escape and body approach phases.

    A via may connect an inward source leg to an outward body leg. Flattening
    those different faces would incorrectly impose a same-face reversal rule.
    """
    project=lambda q,d:sum(q[i]*d[i] for i in (0,1))
    def phase(paths,sign):
        if not paths:return True
        direction=tuple(sign*v for v in h)
        for _,path in paths:
            if any(project(a,direction)>=project(b,direction) for a,b in zip(path,path[1:])):return False
        def transverse(path,x):
            if x<=project(path[0],direction):return project(path[0],t)
            for a,b in zip(path,path[1:]):
                ax,bx=project(a,direction),project(b,direction)
                if ax<=x<=bx:
                    return project(a,t)+(project(b,t)-project(a,t))*(x-ax)/(bx-ax)
            return project(path[-1],t)
        ordered=sorted(paths,key=lambda q:project(lanes[q[0]]['source'],t))
        knots={project(q,direction) for _,path in paths for q in path}
        return all(transverse(a,x)<transverse(b,x) for (_,a),(_,b) in zip(ordered,ordered[1:]) for x in knots)
    source=[(i,r['legs'][0][1]) for i,r in enumerate(routes) if r['vias']]
    body=[(i,r['legs'][-1][1]) for i,r in enumerate(routes)]
    return phase(source,escape_sign) and phase(body,1)


def joint_source_proposals(lanes,*,via_width_nm,clearance_nm,deadline,limit=64,via_drill_nm=0):
    """Return at most 54 ordered proposals, interleaving outward/inward escapes.

    Required lane keys: ``net, source, source_layer, target, target_layer,
    heading, width``. Pad callers must supply directional land extents
    ``source_forward_extent_nm`` / ``source_backward_extent_nm`` measured from
    the actual source along +/- heading. Optional ``source_via_copper_margin_nm``
    and ``source_drill_land_margin_nm`` carry current Native/process margins.
    ``source_track_margin_nm`` carries the actual first-bend copper clearance.
    Zero land extents are reserved for non-pad terminals. Via diameter and drill
    are actual dimensions. Geometry is only a proposal; Native remains final.

    Inward escapes exist only when every source changes face. They preserve
    transverse order on the source face, cross a coordinated via comb, then
    approach the body forward on its face. No same-face U-turn is constructed.
    """
    if type(limit) is not int or not 1<=limit<=64:raise ValueError('Joint proposal limit must be 1..64')
    if (type(via_width_nm) is not int or via_width_nm<=0 or type(clearance_nm) is not int or clearance_nm<0
            or type(via_drill_nm) is not int or not 0<=via_drill_nm<via_width_nm):
        raise ValueError('Physical via diameter, drill and clearance required')
    for lane in lanes:
        extent_keys={'source_forward_extent_nm','source_backward_extent_nm'}
        if ('source_pad_id' in lane or extent_keys & lane.keys()) and not extent_keys<=lane.keys():
            raise ValueError('Pad terminals require both directional source land extents')
        for key in ('source_forward_extent_nm','source_backward_extent_nm','source_track_margin_nm','source_via_copper_margin_nm','source_drill_land_margin_nm'):
            if type(lane.get(key,0)) is not int or lane.get(key,0)<0:raise ValueError('Nonnegative native source land extents/margins required')
    h,t,order,end,width=_inventory(lanes)
    axis=0 if h[0] else 1;sign=h[axis]
    project=lambda q,d:sum(q[i]*d[i] for i in (0,1))
    point=lambda x,y:tuple(h[i]*x+t[i]*y for i in (0,1))
    changing=any(q['source_layer']!=q['target_layer'] for q in lanes)
    all_change=all(q['source_layer']!=q['target_layer'] for q in lanes)
    neck=math.ceil((via_width_nm+width)/2+clearance_nm)+1 if changing else width+clearance_nm+1
    source_pitch=min((project(lanes[b]['source'],t)-project(lanes[a]['source'],t)
                      for a,b in zip(order,order[1:])),default=via_width_nm+clearance_nm)
    stagger=max(neck,math.ceil(math.sqrt(max(0,(via_width_nm+clearance_nm)**2-source_pitch**2))))
    rank={i:k for k,i in enumerate(order)}
    # Three rail profiles x three shifts x three launches x two escape sides.
    # Interleaving keeps both directions available even with a small caller cap.
    rails=[('direct',0)] if not changing else [('common',0),('ascending',1),('descending',-1)]
    answer=[];seen=set()
    for source_shift in ((0,neck,-neck) if changing else (0,)):
        for rail_name,stagger_sign in rails:
            side_plans=[]
            for side in ((1,-1) if all_change else (1,)):
                if time.monotonic()>=deadline:return answer
                extent_key='source_forward_extent_nm' if side==1 else 'source_backward_extent_nm'
                extents=[q.get(extent_key,0) for q in lanes]
                bends=[extent+math.ceil(width/2+max(clearance_nm,q.get('source_track_margin_nm',0)))+1 for extent,q in zip(extents,lanes)]
                via_distances=[extent+math.ceil(max(via_width_nm/2+max(clearance_nm,q.get('source_via_copper_margin_nm',0)),
                    via_drill_nm/2+q.get('source_drill_land_margin_nm',0)))+1 for extent,q in zip(extents,lanes)]
                base=max(side*project(q['source'],h)+max(via_distance,bend)+abs(source_shift)+(neck if source_shift else 0)
                    for q,via_distance,bend in zip(lanes,via_distances,bends))
                via_x=[]
                for i,q in enumerate(lanes):
                    offset=(rank[i] if stagger_sign>0 else len(lanes)-1-rank[i]) if stagger_sign else 0
                    via_x.append(side*(base+offset*stagger) if changing else project(q['source'],h))
                earliest=max((via_x[i]+neck if q['source_layer']!=q['target_layer'] else project(q['source'],h)+bends[i]) for i,q in enumerate(lanes))
                deltas=[project(q['target'],t)-project(q['source'],t)-(source_shift if q['source_layer']!=q['target_layer'] else 0) for q in lanes]
                latest=min(end-neck-abs(d) for d in deltas)
                if latest>=earliest:side_plans.append((side,via_x,bends,earliest,latest))
            for schedule in ('early','center','late'):
                for side,via_x,bends,earliest,latest in side_plans:
                    if time.monotonic()>=deadline or len(answer)>=limit:return answer
                    launch={'early':earliest,'center':(earliest+latest)//2,'late':latest}[schedule]
                    routes=[]
                    for i,q in enumerate(lanes):
                        if time.monotonic()>=deadline:return answer
                        start=tuple(q['source']);via=point(via_x[i],project(start,t)+source_shift)
                        changes=q['source_layer']!=q['target_layer'];origin=via if changes else start
                        path=escape_path(origin,axis,sign,sign*end,q['target'][1-axis]-origin[1-axis],launch-project(origin,h))
                        if path is None:break
                        source_path=escape_path(start,axis,sign*side,sign*via_x[i],via[1-axis]-start[1-axis],bends[i]) if changes else None
                        if changes and source_path is None:break
                        legs=([(q['source_layer'],source_path)] if changes else [])+[(q['target_layer'],path)]
                        routes.append(dict(legs=legs,vias=[via] if changes else []))
                    if len(routes)!=len(lanes) or not _ordered(routes,lanes,h,t,side):continue
                    key=repr(routes)
                    if key in seen:continue
                    seen.add(key);answer.append(dict(routes=routes,rail=rail_name,launch=schedule,
                        via_rails_nm=via_x if changing else [],neck_nm=neck,source_shift_nm=source_shift,
                        escape_side='outward' if side==1 else 'inward'))
    return answer


def _bounded_evidence(value,depth=0):
    """Keep collision receipts finite even if the oracle contains a long log."""
    if depth>=4:return str(value)[:240]
    if isinstance(value,dict):return {str(k)[:80]:_bounded_evidence(v,depth+1) for k,v in list(value.items())[:8]}
    if isinstance(value,(list,tuple)):return [_bounded_evidence(v,depth+1) for v in value[:8]]
    if value is None or isinstance(value,(bool,int,float)):return value
    return str(value)[:240]


def admit_joint_sources(world,lanes,*,via_width_nm,clearance_nm,deadline,checker,limit=64,via_drill_nm=0):
    """Atomically admit a whole proposal via the caller's full physical checker.

    ``checker(world, route, lane) -> (accepted, evidence)`` validates and emits
    one lane into ghost copper. Subsequent lanes therefore see every earlier
    lane. Any rejection/exception restores *all* proposal operations and indexes.
    The successful receipt exports each net's actual ordered emission operations.
    """
    result=dict(accepted=False,accepted_strategy=None,attempts=[],proposal_count=0,
        source_lands=[{k:q[k] for k in ('net','source_forward_extent_nm','source_backward_extent_nm','source_pad_id','source_pad_bbox_nm','source_terminal_kind','source_terminal_id','source_terminal_width_nm','source_terminal_drill_nm','source_track_margin_nm','source_via_copper_margin_nm','source_drill_land_margin_nm') if k in q} for q in lanes])
    try:proposals=joint_source_proposals(lanes,via_width_nm=via_width_nm,clearance_nm=clearance_nm,deadline=deadline,limit=limit,via_drill_nm=via_drill_nm)
    except ValueError as error:
        result['reason']=str(error);return result
    result['proposal_count']=len(proposals)
    mark=world.checkpoint();old_width=world.track_width
    for index,proposal in enumerate(proposals):
        if time.monotonic()>=deadline:result['reason']='joint-source-deadline';break
        world.restore(mark);operations={};accepted=False
        attempt=dict(index=index,rail=proposal['rail'],launch=proposal['launch'],via_rails_nm=proposal['via_rails_nm'],source_shift_nm=proposal['source_shift_nm'],escape_side=proposal['escape_side'])
        try:
            for lane,route in zip(lanes,proposal['routes']):
                if time.monotonic()>=deadline:
                    attempt.update(reason='joint-source-deadline',net=lane['net']);break
                world.track_width=lane['width'];start=len(world.operations)
                okay,evidence=checker(world,route,lane)
                if not okay:
                    attempt.update(reason=evidence.get('reason','physical-rejection'),net=lane['net'],evidence=_bounded_evidence(evidence));break
                operations[lane['net']]=deepcopy(world.operations[start:])
            else:
                if time.monotonic()<deadline:accepted=True
                else:attempt['reason']='joint-source-deadline'
            result['attempts'].append(attempt)
            if accepted:
                attempt['accepted']=True
                result.update(accepted=True,accepted_strategy='joint',escape_side=proposal['escape_side'],operations=operations,reason=None)
                return result
        finally:
            world.track_width=old_width
            if not accepted:world.restore(mark)
    result.setdefault('reason','joint-source-exhausted' if proposals else 'No ordered monotone source transition fits')
    return result
