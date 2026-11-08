"""Preferred monotone vertical/45-degree paths for matrix column fields.

Horizontal shelves are forbidden. Finite monotone candidates are the first
choice, but a no-horizontal graph detour may reverse Y to avoid real obstacles.
"""
import math


def backbone_leg_allowed(path,sign_y):
    if sign_y not in (-1,1,None):raise ValueError('Column direction must be up, down, or unconstrained')
    for a,b in zip(path,path[1:]):
        dx,dy=b[0]-a[0],b[1]-a[1]
        if not dx and not dy:continue
        if not dy or (sign_y is not None and dy*sign_y<=0) or (dx and abs(dx)!=abs(dy)):return False
    return True


def horizontal_length(path):
    return sum(abs(z[0]-a[0]) for a,z in zip(path,path[1:]) if a[1]==z[1])


def preferred_paths(a,b):
    a,b=tuple(a),tuple(b);dx,dy=b[0]-a[0],b[1]-a[1]
    if a==b:return []
    if abs(dx)>abs(dy):return []
    if not dx:return [[a,b]]
    sign=1 if dy>0 else -1;run=abs(dy)-abs(dx)
    # Exact ends first, then a shared row-band middle and bounded phase shifts.
    leads=[0,run,run//2]
    step=max(500000,math.ceil(run/32))
    for i in range(1,17):leads.extend([run//2+i*step,run//2-i*step])
    out=[];seen=set()
    for lead in leads:
        if not 0<=lead<=run:continue
        raw=[a,(a[0],a[1]+sign*lead),(b[0],a[1]+sign*(lead+abs(dx))),b]
        path=[q for i,q in enumerate(raw) if not i or q!=raw[i-1]];key=tuple(path)
        if key in seen:continue
        seen.add(key);out.append(path)
    return out


def column_paths(a,b):
    """Finite V/D paths with two diagonal legs around an intervening obstacle."""
    a,b=tuple(a),tuple(b);dx=b[0]-a[0];dy=b[1]-a[1]
    out={tuple(p) for p in preferred_paths(a,b)}
    if not dy or abs(dx)>abs(dy):return [list(p) for p in sorted(out)]
    sy=1 if dy>0 else -1;spare=abs(dy)-abs(dx)
    lo=min(a[0],b[0])-spare//2;hi=max(a[0],b[0])+spare//2;step=max(500000,math.ceil((hi-lo)/48))
    xs={a[0],b[0],(a[0]+b[0])//2,lo,hi}|set(range(lo,hi+1,step))
    def simplify(raw):
        path=[]
        for q in raw:
            if path and q==path[-1]:continue
            while len(path)>1:
                u=(path[-1][0]-path[-2][0],path[-1][1]-path[-2][1]);v=(q[0]-path[-1][0],q[1]-path[-1][1])
                if u[0]*v[1]!=u[1]*v[0] or u[0]*v[0]+u[1]*v[1]<=0:break
                path.pop()
            path.append(q)
        return path
    for x in sorted(xs):
        first,last=abs(x-a[0]),abs(b[0]-x);vertical=abs(dy)-first-last
        if vertical<0:continue
        for pre in (0,vertical//4,vertical//2):
            for post in (0,vertical//4,vertical//2):
                middle=vertical-pre-post
                if middle<0:continue
                p=simplify([a,(a[0],a[1]+sy*pre),(x,a[1]+sy*(pre+first)),(x,b[1]-sy*(post+last)),(b[0],b[1]-sy*post),b])
                if not backbone_leg_allowed(p,sy):continue
                valid=True
                for u,v,w in zip(p,p[1:],p[2:]):
                    aa=(v[0]-u[0],v[1]-u[1]);bb=(w[0]-v[0],w[1]-v[1]);dot=aa[0]*bb[0]+aa[1]*bb[1]
                    if dot<=0 or 2*dot*dot<(aa[0]**2+aa[1]**2)*(bb[0]**2+bb[1]**2):valid=False;break
                if valid:out.add(tuple(p))
    return [list(p) for p in sorted(out)]


def row_channels(center,low,high,obstacle_bounds,step=250000):
    """Rank row-grid samples plus exact obstacle boundary/gap events."""
    center=round(center);low=math.ceil(low);high=math.floor(high)
    if low>high or step<=0:raise ValueError('Invalid row channel domain')
    critical={low,high,center}
    for a,b in obstacle_bounds:
        if low<=a<=high:critical.add(round(a))
        if low<=b<=high:critical.add(round(b))
    ordered=sorted(critical)
    candidates=set(critical)|{(a+b)//2 for a,b in zip(ordered,ordered[1:])}
    candidates.update(range(center-((center-low)//step)*step,high+1,step))
    return sorted((y for y in candidates if low<=y<=high),key=lambda y:(abs(y-center),y))


def row_domain(pad_y,key_y,pitch):
    """A row channel belongs to its keys as well as offset diode terminals."""
    ys=[*pad_y,*key_y]
    if not ys or pitch<=0:raise ValueError('Invalid row geometry')
    return math.ceil(min(ys)-pitch/2),math.floor(max(ys)+pitch/2)


def column_bridge_paths(a,b,*,rail_ys,limit=64):
    """Three V/45 carriers; each bend MUST be a physical layer-change via.

    Unlike the preferred monotone family, the vertical legs may reverse Y.
    These are three independent straight carriers, never one sharp same-face
    polyline. The Native producer must prove corners, all bridges and contacts.
    """
    a,b=tuple(a),tuple(b)
    if any(type(v) is not int for q in (a,b) for v in q) or len(a)!=2 or len(b)!=2:
        raise ValueError('Integer nm endpoints required')
    if type(limit) is not int or not 1<=limit<=256:raise ValueError('Finite positive proposal cap required')
    from .bundle_geometry import _distance
    proposals=set()
    events=set(rail_ys)
    if any(type(q) is not int for q in events):raise ValueError('Integer physical Y events required')
    def offer(start,end,reverse):
        for rail in events:
            lead=abs(rail-start[1])
            if not lead:continue
            for shift in (0,-lead,lead):
                first=(start[0]+shift,rail);dx=abs(end[0]-first[0])
                if not dx:continue
                for sign in (-1,1):
                    second=(end[0],rail+sign*dx)
                    if second==end:continue
                    legs=((start,first),(first,second),(second,end))
                    directions=[(z[0]-u[0],z[1]-u[1]) for u,z in legs]
                    if any(u[0]*v[1]==u[1]*v[0] and u[0]*v[0]+u[1]*v[1]<0 for u,v in zip(directions,directions[1:])):continue
                    if _distance(*legs[0],*legs[-1])<1:continue
                    if reverse:legs=tuple((z,u) for u,z in reversed(legs))
                    proposals.add(legs)
    offer(a,b,False);offer(b,a,True)
    from collections import defaultdict
    import itertools
    groups=defaultdict(list)
    for legs in proposals:
        topology=tuple(((z[0]-u[0])//math.gcd(z[0]-u[0],z[1]-u[1]),(z[1]-u[1])//math.gcd(z[0]-u[0],z[1]-u[1])) for u,z in legs)
        groups[topology].append(legs)
    rank=lambda legs:(sum(math.dist(u,v) for u,v in legs),max(math.dist(*legs[0]),math.dist(*legs[-1])),legs)
    families=[sorted(q,key=rank) for q in groups.values()]
    families.sort(key=lambda q:rank(q[0]))
    # Best first, then cover physically different corner positions. Hundreds
    # of nanometre-close pad events are not hundreds of useful alternatives.
    per_family=(limit+max(1,len(families))-1)//max(1,len(families))
    diverse=[]
    for rows in families:
        chosen=[rows[0]];remaining={q:float('inf') for q in rows[1:]}
        while remaining and len(chosen)<per_family:
            last=chosen[-1]
            for q in remaining:
                distance=sum((q[j][1][i]-last[j][1][i])**2 for j in (0,1) for i in (0,1))
                remaining[q]=min(remaining[q],distance)
            next_row=min(remaining,key=lambda q:(-remaining[q],rank(q)))
            chosen.append(next_row);del remaining[next_row]
        diverse.append(chosen)
    # Each topology and each physical event region shares the caller's cap.
    ordered=[q for group in itertools.zip_longest(*diverse) for q in group if q is not None]
    return [[list(leg) for leg in legs] for legs in ordered[:limit]]
