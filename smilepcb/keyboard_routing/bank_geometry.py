"""Ordered MCU escape geometry recovered from the public-reference study.

Pure proposals in native nanometres. Native clearance and contact proof remain
mandatory before any proposal can be emitted.
"""


def port_outside_bounds(point,heading,bounds,width_nm):
    """The entire terminal cap must clear the frame in its departure direction."""
    if heading not in ((1,0),(-1,0),(0,1),(0,-1)):
        raise ValueError('Controller departure must be cardinal')
    axis=0 if heading[0] else 1;sign=heading[axis]
    edge=bounds[axis+(2 if sign>0 else 0)]
    return (point[axis]-edge)*sign>=width_nm//2+1


def escape_path(origin,axis,sign,rail,tangent_shift,neck_nm):
    if axis not in (0,1) or sign not in (-1,1) or neck_nm<=0:raise ValueError('Invalid physical bank geometry')
    origin=tuple(origin);normal=(rail-origin[axis])*sign
    if normal<neck_nm+abs(tangent_shift):return None
    launch=list(origin);launch[axis]+=sign*neck_nm
    elbow=launch[:];elbow[axis]+=sign*abs(tangent_shift);elbow[1-axis]+=tangent_shift
    end=elbow[:];end[axis]=rail
    return clean_port_path([origin,tuple(launch),tuple(elbow),tuple(end)])


def clean_port_path(points):
    import math
    out=[]
    def heading(a,b):
        dx,dy=b[0]-a[0],b[1]-a[1];g=math.gcd(dx,dy)
        return (dx//g,dy//g) if g else None
    for q in map(tuple,points):
        if out and q==out[-1]:continue
        while len(out)>1 and heading(out[-2],out[-1])==heading(out[-1],q):out.pop()
        out.append(q)
    previous=None
    for a,b in zip(out,out[1:]):
        d=heading(a,b)
        if d is None or max(map(abs,d))!=1:return None
        if previous is not None:
            dot=sum(x*y for x,y in zip(previous,d))
            if dot<=0 or 2*dot*dot<sum(x*x for x in previous)*sum(x*x for x in d):return None
        previous=d
    return out if len(out)>1 else None


def compatible_bank(a,b,*,same_layer=True):
    # Cohorts bind physical source pad identities, so GPIO reassignment cannot
    # turn a mixed/independent via lane into a coherent family accidentally.
    for left,right in ((a,b),(b,a)):
        if left.get('cohort') is not None and set(left.get('cohort_pad_ids',())) & set(right.get('pad_ids',())):
            if (left['cohort']!=right.get('cohort') or left.get('cohort_profile') is None
                    or left.get('cohort_profile')!=right.get('cohort_profile')):return False
    return not (same_layer and a.get('kind')==b.get('kind')=='same_layer' and
                a['bank']==b['bank'] and a['profile']!=b['profile'])


def terminal_origin(port):
    origin=port.get('heading_origin_nm')
    if origin is None:origin=port.get('via_nm')
    if origin is None:raise ValueError('Missing physical port heading origin')
    return tuple(origin)


def port_is_retired(port,live):
    key='terminal_id' if port.get('kind')=='same_layer' else 'via_id'
    uid=port.get(key)
    if not isinstance(uid,str) or not uid:raise ValueError('Missing physical port terminal identity')
    return uid not in live


def pitch_transition(starts,axis,sign,rail,neck_nm,pitch_nm):
    """A finite monotone launch region may convert pad pitch into lane pitch."""
    if not starts or type(pitch_nm) is not int or pitch_nm<=0:
        raise ValueError('Positive native lane pitch and physical starts required')
    tangent=1-axis;order=sorted(range(len(starts)),key=lambda i:starts[i][tangent])
    center=(starts[order[0]][tangent]+starts[order[-1]][tangent])/2;result=[None]*len(starts)
    for rank,index in enumerate(order):
        desired=round(center+(rank-(len(starts)-1)/2)*pitch_nm)
        result[index]=escape_path(starts[index],axis,sign,rail,desired-starts[index][tangent],neck_nm)
    return result if all(result) else None


def bank_allocation(banks,needs,preferred=(),*,uncertain_pins=(),unwitnessed_pins=()):
    """Use real exclusive-bank capacity before optimizing physical lane order."""
    sides=[s for s in ('N','E','S','W') if s in banks];families=list(needs)
    flat=[pin for side in sides for pin in banks[side]]
    if set(banks)-set(sides) or len(flat)!=len(set(flat)):
        raise ValueError('Ambiguous physical GPIO bank inventory')
    if any(type(n) is not int or n<0 for n in needs.values()) or set(preferred)-set(sides):
        raise ValueError('Invalid physical bank capacity/preference')
    uncertain=set(uncertain_pins)
    if uncertain-set(flat):raise ValueError('Unknown uncertain GPIO')
    unwitnessed=set(unwitnessed_pins)
    if unwitnessed-set(flat):raise ValueError('Unknown unwitnessed GPIO')
    banks={side:sorted(banks[side],key=lambda pin:(pin in uncertain,pin in unwitnessed)) for side in sides}
    best=None;answer=None
    def visit(i,left,chosen,unproven,missed,groups,side_cost):
        nonlocal best,answer
        if best is not None and (unproven,missed,groups,side_cost)>best[:4]:return
        if sum(left.values())>sum(len(banks[s]) for s in sides[i:]):return
        if i==len(sides):
            if any(left.values()):return
            score=(unproven,missed,groups,side_cost,repr(chosen))
            if best is None or score<best:
                best=score;answer={f:[] for f in families}
                for side,f,count in chosen:
                    if f is not None:answer[f].extend(banks[side][:count])
            return
        side=sides[i];visit(i+1,left,chosen+[(side,None,0)],unproven,missed,groups,side_cost)
        penalty=0 if side in preferred else 1
        for f in families:
            for count in range(1,min(left[f],len(banks[side]))+1):
                visit(i+1,{**left,f:left[f]-count},chosen+[(side,f,count)],unproven+sum(g in uncertain for g in banks[side][:count]),missed+sum(g in unwitnessed for g in banks[side][:count]),groups+1,side_cost+count*penalty)
    visit(0,dict(needs),[],0,0,0,0)
    return answer


def exit_permutation(ports,targets,families):
    """Assign signals within their role using the final physical lane order."""
    from collections import defaultdict
    names=[q['net'] for q in ports]
    if len(set(names))!=len(names) or not set(names)<=set(targets) or not set(names)<=set(families):
        raise ValueError('Incomplete or ambiguous final-lane inventory')
    groups=defaultdict(list);result={n:n for n in names}
    for port in ports:
        a=terminal_origin(port);b=port['exit_nm'];dx,dy=b[0]-a[0],b[1]-a[1]
        if not dx and not dy:raise ValueError('Missing physical lane departure')
        heading=((dx>0)-(dx<0),(dy>0)-(dy<0));bank=tuple(port.get('bank') or ('isolated',port['net']))
        family=families[port['net']]
        if family not in ('rows','columns'):raise ValueError('Only actual matrix roles may be permuted')
        groups[family,bank,port['exit_layer'],heading].append(port)
    for (family,*_),group in groups.items():
        if len(group)<2:continue
        axis=max((0,1),key=lambda a:max(q['exit_nm'][a] for q in group)-min(q['exit_nm'][a] for q in group))
        lanes=sorted(group,key=lambda q:(q['exit_nm'][axis],q['net']))
        signals=sorted((q['net'] for q in group),key=lambda n:(targets[n][1 if family=='rows' else 0],n))
        result.update((q['net'],name) for q,name in zip(lanes,signals))
    return result
