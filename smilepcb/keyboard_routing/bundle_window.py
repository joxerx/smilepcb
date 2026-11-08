"""Cut continuous layered copper by arclength without changing fixed geometry."""
import math


def _append(legs,face,a,z):
    if a==z:return
    if legs and legs[-1][0]==face and legs[-1][1][-1]==a:legs[-1][1].append(z)
    else:legs.append((face,[a,z]))


def slice_legs(legs,lo_nm,hi_nm):
    edges=[];vias=[];cursor=0.;last=None;last_face=None
    for face,path in legs:
        path=list(map(tuple,path))
        if not path:continue
        if last is not None:
            if path[0]!=last:raise ValueError('Layered source must be continuous')
            if face!=last_face:vias.append((cursor,path[0]))
        for a,z in zip(path,path[1:]):
            distance=math.dist(a,z)
            if distance:edges.append((cursor,cursor+distance,face,a,z));cursor+=distance
        last=path[-1];last_face=face
    if not 0<=lo_nm<hi_nm<=cursor+1e-6 or not edges:raise ValueError('Body cuts must lie on an open layered route')
    hi_nm=min(hi_nm,cursor)
    def point(edge,d):
        lo,hi,_,a,z=edge;t=max(0,min(1,(d-lo)/(hi-lo)))
        return tuple(round(a[i]+(z[i]-a[i])*t) for i in (0,1))
    source_edge=next(e for e in edges if e[0]<=lo_nm<e[1])
    target_edge=next(e for e in edges if e[0]<hi_nm<=e[1]+1e-6)
    body=[];prefix=[];suffix=[]
    for e in edges:
        lo,hi,face,a,z=e
        for left,right,dest in [(lo,min(hi,lo_nm),prefix),(max(lo,lo_nm),min(hi,hi_nm),body),(max(lo,hi_nm),hi,suffix)]:
            if right-left>1e-6:_append(dest,face,point(e,left),point(e,right))
    return dict(source=point(source_edge,lo_nm),target=point(target_edge,hi_nm),source_layer=source_edge[2],target_layer=target_edge[2],body_legs=body,fixed_legs=prefix+suffix,prefix_legs=prefix,suffix_legs=suffix,
                fixed_vias=[q for d,q in vias if d<=lo_nm+1e-6 or d>=hi_nm-1e-6],body_vias=[q for d,q in vias if lo_nm<d<hi_nm],length_nm=cursor,lo_nm=lo_nm,hi_nm=hi_nm)


def first_exit_distance(paths,bounds):
    x0,y0,x1,y1=bounds;distance=0.
    for path in paths:
        for a,z in zip(path,path[1:]):
            if not x0<=a[0]<=x1 or not y0<=a[1]<=y1:return distance
            span=math.dist(a,z)
            if not span:continue
            if x0<=z[0]<=x1 and y0<=z[1]<=y1:distance+=span;continue
            ts=[1.]
            for i,low,high in [(0,x0,x1),(1,y0,y1)]:
                delta=z[i]-a[i]
                if delta>0:ts.append((high-a[i])/delta)
                elif delta<0:ts.append((low-a[i])/delta)
            return distance+max(0,min(ts))*span
    return distance
