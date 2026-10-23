"""Keep junctions that the DSN integer grid cannot represent exactly.

This changes source mobility only. Coordinates, copper dimensions and native
rules are never changed, and a complete exchange roundtrip is still required.
"""
from collections import defaultdict
from fractions import Fraction
import time

def ambiguous_junction_ids(tracks,*,quantum_nm,deadline=None):
    if type(quantum_nm) is not int or quantum_nm<=0:raise ValueError('Expected a positive integer exchange quantum')
    if not isinstance(tracks,dict) or len(tracks)>10000:raise ValueError('Expected a bounded source copper inventory')
    groups=defaultdict(list)
    for uid,item in tracks.items():
        if item['class']!='PCB_TRACK':continue
        a,b=item['start'],item['end']
        if len(a)!=2 or len(b)!=2 or any(type(x) is not int for x in (*a,*b)):raise ValueError('Source track coordinates must be native integers')
        if a==b:continue
        box=(min(a[0],b[0]),min(a[1],b[1]),max(a[0],b[0]),max(a[1],b[1]))
        groups[item['net'],item['layer']].append((box,uid,a,b))
    result=set()
    for rows in groups.values():
        active=[]
        for box,uid,a,b in sorted(rows):
            if deadline is not None and time.monotonic()>=deadline:raise TimeoutError('Source junction classification deadline exhausted')
            active=[r for r in active if r[0][2]>=box[0]]
            dx,dy=b[0]-a[0],b[1]-a[1]
            for other,vid,c,d in active:
                if box[1]>other[3] or other[1]>box[3]:continue
                ex,ey=d[0]-c[0],d[1]-c[1];den=dx*ey-dy*ex
                if not den:continue
                t=Fraction((c[0]-a[0])*ey-(c[1]-a[1])*ex,den)
                u=Fraction((c[0]-a[0])*dy-(c[1]-a[1])*dx,den)
                if not (0<t<1 and 0<u<1):continue
                point=(Fraction(a[0])+t*dx,Fraction(a[1])+t*dy)
                if any(q.denominator!=1 or int(q)%quantum_nm for q in point):result.update((uid,vid))
            active.append((box,uid,a,b))
    return result


def endpoint_protection_closure(tracks,protected,*,deadline=None):
    """Keep exact endpoint-connected trace chains under one mobility policy.

    Native DSN export can coalesce touching fixed and editable straight
    segments into a single fixed path. Preserve every member of such a chain
    before export so fixed exchange omissions cannot delete an editable native
    member. Only equal net/layer/width segments sharing an exact endpoint join;
    crossings, pads, vias and zero-length copper dots do not propagate policy.
    Neither caller inventory nor its protected collection is mutated.
    """
    import math
    if not isinstance(tracks,dict) or len(tracks)>10000:
        raise ValueError('Expected a bounded source copper inventory')
    if not isinstance(protected,(set,frozenset,list,tuple)) or len(protected)>10000:
        raise ValueError('Expected bounded protected source IDs')
    if deadline is not None and (type(deadline) not in (int,float) or not math.isfinite(deadline)):
        raise ValueError('Expected a finite native protection deadline')
    def budget():
        if deadline is not None and time.monotonic()>=deadline:
            raise TimeoutError('Endpoint protection closure deadline exhausted')
    budget()
    result=set()
    for uid in protected:
        budget()
        if type(uid) is not str or not uid or uid not in tracks:
            raise ValueError('Protected source copper is missing or invalid')
        result.add(uid)
    endpoints=defaultdict(list);segments={}
    for uid,item in tracks.items():
        budget()
        if type(uid) is not str or not uid or not isinstance(item,dict) or type(item.get('class')) is not str:
            raise ValueError('Invalid source copper identity/class')
        if item['class']!='PCB_TRACK':continue
        a,b=item.get('start'),item.get('end')
        if (not isinstance(a,(list,tuple)) or not isinstance(b,(list,tuple)) or
                len(a)!=2 or len(b)!=2 or any(type(value) is not int for value in (*a,*b))):
            raise ValueError('Source track coordinates must be native integers')
        net,layer,width=item.get('net'),item.get('layer'),item.get('width')
        if (type(net) is not str or type(layer) not in (int,str) or
                type(width) is not int or width<=0):
            raise ValueError('Source tracks require native net, layer and positive width')
        if tuple(a)==tuple(b):continue
        keys=((net,layer,width,tuple(a)),(net,layer,width,tuple(b)))
        segments[uid]=keys
        for key in keys:endpoints[key].append(uid)
    pending=[uid for uid in result if uid in segments]
    while pending:
        budget();uid=pending.pop()
        for key in segments[uid]:
            budget()
            # Consume each endpoint bucket once, keeping dense stars linear.
            for neighbour in endpoints.pop(key,()):
                budget()
                if neighbour not in result:
                    result.add(neighbour);pending.append(neighbour)
    return result
