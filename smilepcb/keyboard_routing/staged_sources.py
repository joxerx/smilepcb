"""Source-only group interfaces for optional source-first corridor planning.

Pure geometry never invents a field target or a common longitudinal outlet
rail. Native admission supplies the trust boundary for transformed row copies.
"""
from copy import deepcopy
import math,time
from .bank_geometry import escape_path,clean_port_path
from .joint_source_transition import CARDINAL,_bounded_evidence


def source_head_proposals(lanes,*,body_face,via_width_nm,via_drill_nm,clearance_nm,deadline,outlet_nm=500000,limit=64):
    """Generate <=18 coherent heads from actual source interfaces, no targets.

    Lane schema and directional land/process margins match joint_source_proposals,
    except target/target_layer are absent. Each head ends with a real outlet_nm
    cardinal run on body_face; its actual endpoint may be longitudinally staggered.
    """
    if type(limit) is not int or not 1<=limit<=64:raise ValueError('Head proposal limit must be 1..64')
    if (type(via_width_nm) is not int or type(via_drill_nm) is not int or not 0<via_drill_nm<via_width_nm
            or type(clearance_nm) is not int or clearance_nm<0 or type(outlet_nm) is not int or outlet_nm<=0):
        raise ValueError('Actual via/drill, clearance and positive outlet length required')
    if not lanes or len({q['net'] for q in lanes})!=len(lanes):raise ValueError('Distinct actual source interfaces required')
    headings={tuple(q['heading']) for q in lanes};widths={q['width'] for q in lanes}
    if len(headings)!=1 or next(iter(headings)) not in CARDINAL:raise ValueError('One cardinal source heading required')
    if len(widths)!=1 or any(type(w) is not int or w<=0 for w in widths):raise ValueError('Uniform native widths required')
    for lane in lanes:
        if len(lane['source'])!=2 or any(type(v) is not int for v in lane['source']):raise ValueError('Native source coordinates required')
        extent_keys={'source_forward_extent_nm','source_backward_extent_nm'}
        if ('source_pad_id' in lane or extent_keys & lane.keys()) and not extent_keys<=lane.keys():raise ValueError('Both source land extents required')
        for key in (*extent_keys,'source_track_margin_nm','source_via_copper_margin_nm','source_drill_land_margin_nm'):
            if type(lane.get(key,0)) is not int or lane.get(key,0)<0:raise ValueError('Nonnegative physical source margins required')
    h=next(iter(headings));t=(-h[1],h[0]);width=next(iter(widths));axis=0 if h[0] else 1;sign=h[axis]
    project=lambda q,d:sum(q[i]*d[i] for i in (0,1))
    point=lambda x,y:tuple(h[i]*x+t[i]*y for i in (0,1))
    order=sorted(range(len(lanes)),key=lambda i:project(lanes[i]['source'],t));rank={i:k for k,i in enumerate(order)}
    positions=[project(lanes[i]['source'],t) for i in order]
    if any(a>=b for a,b in zip(positions,positions[1:])):raise ValueError('Distinct ordered transverse source positions required')
    neck=math.ceil((via_width_nm+width)/2+clearance_nm)+1
    pitch=min((b-a for a,b in zip(positions,positions[1:])),default=via_width_nm+clearance_nm)
    stagger=max(neck,math.ceil(math.sqrt(max(0,(via_width_nm+clearance_nm)**2-pitch**2))))
    all_change=all(q['source_layer']!=body_face for q in lanes)
    answer=[];seen=set()
    for shift in (0,neck,-neck):
        for rail_name,stagger_sign in (('common',0),('ascending',1),('descending',-1)):
            for side in ((1,-1) if all_change else (1,)):
                if time.monotonic()>=deadline or len(answer)>=limit:return answer
                extents=[q.get('source_forward_extent_nm' if side==1 else 'source_backward_extent_nm',0) for q in lanes]
                bends=[e+math.ceil(width/2+max(clearance_nm,q.get('source_track_margin_nm',0)))+1 for e,q in zip(extents,lanes)]
                distances=[e+math.ceil(max(via_width_nm/2+max(clearance_nm,q.get('source_via_copper_margin_nm',0)),
                    via_drill_nm/2+q.get('source_drill_land_margin_nm',0)))+1 for e,q in zip(extents,lanes)]
                base=max(side*project(q['source'],h)+max(bend,distance)+abs(shift)+(neck if shift else 0)
                    for q,bend,distance in zip(lanes,bends,distances))
                routes=[];rails=[]
                for i,q in enumerate(lanes):
                    if time.monotonic()>=deadline:return answer
                    offset=(rank[i] if stagger_sign>0 else len(lanes)-1-rank[i]) if stagger_sign else 0
                    x=side*(base+offset*stagger);via=point(x,project(q['source'],t)+shift)
                    source_path=escape_path(q['source'],axis,sign*side,sign*x,via[1-axis]-q['source'][1-axis],bends[i])
                    if source_path is None:break
                    outlet=tuple(via[k]+h[k]*outlet_nm for k in (0,1));changes=q['source_layer']!=body_face
                    if changes:legs=[(q['source_layer'],source_path),(body_face,[via,outlet])]
                    else:
                        path=clean_port_path([*source_path,outlet])
                        if path is None:break
                        legs=[(body_face,path)]
                    routes.append(dict(legs=legs,vias=[via] if changes else []));rails.append(x)
                if len(routes)!=len(lanes):continue
                signature=repr(routes)
                if signature in seen:continue
                seen.add(signature);answer.append(dict(routes=routes,rail=rail_name,via_rails_nm=rails,source_shift_nm=shift,
                    escape_side='outward' if side==1 else 'inward',outlet_nm=outlet_nm))
    return answer


def admit_source_heads(world,lanes,*,body_face,via_width_nm,via_drill_nm,clearance_nm,deadline,checker,outlet_nm=500000,limit=64):
    """Atomically emit one entire head group through the full physical checker.

    The caller's absolute deadline governs generation and every lane admission.
    A success is a candidate interface only, never a complete corridor result.
    """
    result=dict(accepted=False,attempts=[],source_lands=deepcopy(lanes),proposal_count=0)
    try:proposals=source_head_proposals(lanes,body_face=body_face,via_width_nm=via_width_nm,via_drill_nm=via_drill_nm,
        clearance_nm=clearance_nm,deadline=deadline,outlet_nm=outlet_nm,limit=limit)
    except ValueError as error:result['reason']=str(error);return result
    result['proposal_count']=len(proposals);mark=world.checkpoint();old_width=world.track_width
    for index,proposal in enumerate(proposals):
        if time.monotonic()>=deadline:break
        world.restore(mark);accepted=False;operations={};attempt={k:v for k,v in proposal.items() if k!='routes'}
        attempt['index']=index;result['attempts'].append(attempt)
        try:
            for lane,route in zip(lanes,proposal['routes']):
                if time.monotonic()>=deadline:attempt['reason']='source-head-deadline';break
                world.track_width=lane['width'];start=len(world.operations);okay,evidence=checker(world,route,lane)
                if not okay:
                    attempt.update(reason=evidence.get('reason','physical-rejection'),net=lane['net'],evidence=_bounded_evidence(evidence));break
                operations[lane['net']]=deepcopy(world.operations[start:])
            else:
                accepted=time.monotonic()<deadline
                if not accepted:attempt['reason']='source-head-deadline'
            if accepted:
                attempt['accepted']=True
                outlets=[dict(net=lane['net'],exit_nm=tuple(route['legs'][-1][1][-1]),exit_layer=route['legs'][-1][0],
                    heading=tuple(lane['heading']),heading_origin_nm=tuple(route['legs'][-1][1][-2]),width_nm=lane['width'])
                    for lane,route in zip(lanes,proposal['routes'])]
                result.update(accepted=True,operations=operations,routes=deepcopy(proposal['routes']),outlets=outlets,
                    escape_side=proposal['escape_side'],reason=None)
                return result
        finally:
            world.track_width=old_width
            if not accepted:world.restore(mark)
    result['reason']='source-head-deadline' if time.monotonic()>=deadline else 'source-head-family-exhausted'
    return result


def staged_group(group,receipt):
    """Transform internal row copies only after actual all-head Native admission.

    New head tracks become complete-route prefix legs. New vias are returned
    separately for assembly; they remain new operations, never fixed witnesses.
    """
    if not receipt.get('accepted'):raise ValueError('No admitted source head group')
    names=[r['net'] for r in group]
    if names!=[q['net'] for q in receipt['outlets']] or set(names)!=set(receipt['operations']):raise ValueError('Head lane identity mismatch')
    result=deepcopy(group);vias={}
    for row,route,outlet in zip(result,receipt['routes'],receipt['outlets']):
        w=row['window']
        if w['prefix_legs'] or 'source_prefix_witness' in row:raise ValueError('Staged heads require direct pad source windows')
        if tuple(route['legs'][0][1][0])!=tuple(w['source']) or route['legs'][0][0]!=w['source_layer']:raise ValueError('Head misses its actual original source')
        w.update(source=tuple(outlet['exit_nm']),source_layer=outlet['exit_layer'],prefix_legs=deepcopy(route['legs']))
        row['heading']=tuple(outlet['heading'])
        vias[row['net']]=[dict(op) for op in receipt['operations'][row['net']] if op['kind']=='via']
        if any(op.get('fixed') for op in vias[row['net']]):raise ValueError('New head via cannot be a retained witness')
    return result,vias
