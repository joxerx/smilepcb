"""Two-layer shortest search along one simple carrier, with native callbacks."""
import math,time
from .follow_geometry import simplify


def bridge_carrier(path,preferred,other,*,clear,via_clear,via_pair_clear,step_nm=250000,via_cost_nm=2000000,deadline=None,start_layers=None,end_layers=None,secondary_length_factor=1.05):
    start_layers={preferred} if start_layers is None else set(start_layers)
    end_layers={preferred} if end_layers is None else set(end_layers)
    if not start_layers or not end_layers or not (start_layers|end_layers)<={preferred,other}:raise ValueError('Invalid endpoint layer access')
    if not math.isfinite(secondary_length_factor) or not 1<=secondary_length_factor<=100:raise ValueError('Invalid preferred layer cost')
    if preferred in start_layers and preferred in end_layers and all(clear(tuple(a),tuple(b),preferred) for a,b in zip(path,path[1:])):
        return {'legs':[(preferred,simplify(path))],'vias':[],'score':sum(math.dist(a,b) for a,b in zip(path,path[1:]))}
    points=[tuple(path[0])]
    for a,b in zip(path,path[1:]):
        steps=max(1,math.ceil(max(abs(b[i]-a[i]) for i in (0,1))/step_nm))
        points.extend(tuple(round(a[j]+(b[j]-a[j])*i/steps) for j in (0,1)) for i in range(1,steps+1))
    # Labels retain planned via geometry: cheaper arrivals with incompatible
    # hole spacing cannot erase a viable alternative. All motion is forward.
    states={face:([(0.,(),((face,(points[0],)),))] if face in start_layers else []) for face in (preferred,other)};via_cache={};edge_cache={}
    for i,(a,b) in enumerate(zip(points,points[1:])):
        if deadline is not None and time.monotonic()>=deadline:return None
        expanded={face:list(rows) for face,rows in states.items()}
        if i and any(states.values()):
            if a not in via_cache:via_cache[a]=via_clear(a)
            if via_cache[a]:
                for face,target in ((preferred,other),(other,preferred)):
                    for cost,vias,legs in states[face]:
                        if all(via_pair_clear(a,v) for v in vias):expanded[target].append((cost+via_cost_nm,(*vias,a),(*legs,(target,(a,)))))
        following={preferred:[],other:[]}
        for face,labels in expanded.items():
            key=(i,face)
            if key not in edge_cache:edge_cache[key]=clear(a,b,face)
            if not edge_cache[key]:continue
            for cost,vias,legs in labels:
                length=math.dist(a,b)*(1 if face==preferred else secondary_length_factor)
                leg=legs[-1];updated=(*legs[:-1],(face,(*leg[1],b)))
                following[face].append((cost+length,vias,updated))
            # Equal histories can have different leg segmentation but identical
            # future feasibility; keep the cheapest, then a small physical beam.
            best={}
            for row in sorted(following[face],key=lambda q:(q[0],q[1],q[2])):best.setdefault(row[1],row)
            following[face]=list(best.values())[:4]
        states=following
        if not any(states.values()):return None
    endings=[q for face in end_layers for q in states[face]]
    if not endings:return None
    cost,vias,legs=min(endings,key=lambda q:(q[0],q[1],q[2]))
    return {'legs':[(face,simplify(q)) for face,q in legs],'vias':list(vias),'score':cost}
