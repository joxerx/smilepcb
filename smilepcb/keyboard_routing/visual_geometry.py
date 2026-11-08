"""Geometry proposals for completed boards; scores never waive native rules."""
from collections import defaultdict
import math
from .follow_geometry import simplify
from .parallel_neighbors import ParallelNeighbors,frame


def column_paths(a,z,*,step_nm=250000,hints=(),limit=192):
    """Monotone V/45/V paths with a sliding turn, never an artificial zigzag."""
    a,z=tuple(a),tuple(z);dx,dy=z[0]-a[0],z[1]-a[1]
    if not dy or abs(dx)>abs(dy):return []
    if not dx:return [[a,z]]
    if type(step_nm) is not int or step_nm<=0:raise ValueError('Positive column sampling step required')
    sy=1 if dy>0 else -1;span=abs(dy)-abs(dx)
    cuts={0,span,span//2,*range(0,span+1,step_nm)}
    for x,y in hints:
        for cut in ((y-a[1])*sy,(y-a[1])*sy-abs(dx)):
            cuts.update(cut+d for d in (-step_nm,0,step_nm) if 0<=cut+d<=span)
    out={tuple(simplify([a,(a[0],a[1]+sy*q),(z[0],a[1]+sy*(q+abs(dx))),z])) for q in cuts}
    return [list(q) for q in sorted(out,key=lambda q:(len(q),abs((q[1][1]-a[1])*sy-span/2),q))[:limit]]


def duplicate_ids(rows,protected=()):
    """Only exact physical duplicates; protected/locked representatives survive."""
    groups=defaultdict(list);protected=set(protected)
    for uid,t in rows.items():
        if t['class']!='PCB_TRACK':continue
        key=(t['net'],t['layer'],t['width'],tuple(sorted((tuple(t['start']),tuple(t['end'])))))
        groups[key].append(uid)
    result=[]
    for ids in groups.values():
        keep={u for u in ids if u in protected or rows[u]['locked']}
        if not keep:keep={min(ids)}
        result.extend(u for u in ids if u not in keep)
    return sorted(result)


class UniformNeighbors(ParallelNeighbors):
    """Continuous foreign adjacency with separate eligibility/preferred spacing."""
    def __init__(self,*args,preferred_edge_gap_nm=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.preferred_edge_gap_nm=self.minimum if preferred_edge_gap_nm is None else preferred_edge_gap_nm
        if not math.isfinite(self.preferred_edge_gap_nm) or self.preferred_edge_gap_nm<0:
            raise ValueError('Preferred neighbour spacing must be finite and nonnegative')

    def coverage(self,a,z,layer,net,width):
        geometry=frame(a,z)
        if geometry is None:return 0.
        _,c,lo,hi=geometry;eligible=[]
        for (_,nc,nwidth),intervals in self.neighbor_intervals(a,z,layer,net,width).items():
            merged=[]
            for u,v in sorted(intervals):
                if merged and u<=merged[-1][1]+1e-6:merged[-1]=(merged[-1][0],max(v,merged[-1][1]))
                else:merged.append((u,v))
            gap=abs(c-nc)-(width+nwidth)/2
            excess=max(0.,gap-self.preferred_edge_gap_nm)
            if excess<1e-6:excess=0.
            weight=1/(1+excess/max(1,self.preferred_edge_gap_nm+(width+nwidth)/2))
            eligible.extend((u,v,weight) for u,v in merged if v-u+1e-6>=self.min_run)
        cuts=sorted({p for u,v,_ in eligible for p in (u,v)})
        return sum((v-u)*max((w for a,b,w in eligible if a<=u+1e-6 and b>=v-1e-6),default=0.) for u,v in zip(cuts,cuts[1:]))


def foreign_guides(origin,segments,*,net,width,gap_nm,limit=16,radius_nm=5000000):
    """Nearest offsets of real foreign carriers, not a hand-drawn bus axis."""
    out=set()
    for row in segments:
        if row['net']==net:continue
        a,z=tuple(row['start']),tuple(row['end']);dx,dy=z[0]-a[0],z[1]-a[1]
        if frame(a,z) is None:continue
        norm=dx*dx+dy*dy
        t=max(0,min(1,sum((origin[i]-a[i])*(z[i]-a[i]) for i in (0,1))/norm))
        foot=(a[0]+t*dx,a[1]+t*dy)
        if math.dist(origin,foot)>radius_nm:continue
        length=math.sqrt(norm);pitch=(width+row['width'])/2+gap_nm
        for sign in (-1,1):
            point=(round(foot[0]-sign*dy/length*pitch),round(foot[1]+sign*dx/length*pitch))
            if point!=tuple(origin):out.add(point)
    return sorted(out,key=lambda q:(math.dist(q,origin),q))[:limit]


def column_bypass_paths(a,z,*,step_nm=500000,limit=192):
    """Two diagonal runs separated by vertical travel can pass hole columns."""
    from .polish_geometry import valid_shape
    a,z=tuple(a),tuple(z);dx,dy=z[0]-a[0],z[1]-a[1]
    if not dy or abs(dx)>abs(dy):return []
    sy=1 if dy>0 else -1;slack=abs(dy)-abs(dx)
    mids={round(a[0]+dx*f/4) for f in (1,2,3)}
    for k in (1,2,4,6):
        offset=k*step_nm
        if 2*offset<=slack:mids.update((min(a[0],z[0])-offset,max(a[0],z[0])+offset))
    out=set()
    for x in sorted(mids):
        d1,d2=abs(x-a[0]),abs(z[0]-x);free=abs(dy)-d1-d2
        if free<0:continue
        for first in sorted({0,free//2,free}):
            rest=free-first
            for middle in sorted({0,rest//2,rest}):
                q=simplify([a,(a[0],a[1]+sy*first),(x,a[1]+sy*(first+d1)),(x,a[1]+sy*(first+d1+middle)),(z[0],a[1]+sy*(first+d1+middle+d2)),z])
                if valid_shape(q) and all(u[1]!=v[1] for u,v in zip(q,q[1:])):out.add(tuple(q))
    return [list(q) for q in sorted(out,key=lambda q:(len(q),sum(math.dist(u,v) for u,v in zip(q,q[1:])),q))[:limit]]


def feeder_paths(query,foreign,*,net,layer,width,gap_nm,limit=192):
    """Join real offset lanes at octile tangent intersections, never at 90 degrees."""
    from .follow_geometry import offset_path
    from .polish_geometry import valid_shape
    from .bundle_geometry import paths_clear
    a,z=tuple(query[0]),tuple(query[-1]);out=set();normals=((1,0),(0,1),(1,1),(1,-1))
    def attachments(path,q):
        result=[]
        for i,(u,v) in enumerate(zip(path,path[1:])):
            dx,dy=v[0]-u[0],v[1]-u[1];g=math.gcd(dx,dy)
            if not g:continue
            n=(-dy//g,dx//g);c=n[0]*u[0]+n[1]*u[1]
            for k in normals:
                d=k[0]*q[0]+k[1]*q[1];det=n[0]*k[1]-n[1]*k[0]
                if not det:continue
                x,y=c*k[1]-n[1]*d,n[0]*d-c*k[0]
                if x%det or y%det:continue
                p=(x//det,y//det);t=((p[0]-u[0])*dx+(p[1]-u[1])*dy)/(dx*dx+dy*dy)
                if 0<=t<=1:result.append((i+t,p))
        return result
    for ref in foreign:
        if ref['net']==net or ref['layer']!=layer:continue
        path=ref['path'];pitch=(width+ref['width'])/2+gap_nm
        for guide in [path,*[[u,v] for u,v in zip(path,path[1:])]]:
            for sign in (-1,1):
                shifted=offset_path(guide,sign*pitch)
                if shifted is None:continue
                for u,p in attachments(shifted,a):
                    for v,q in attachments(shifted,z):
                        if u==v:continue
                        middle=[tuple(x) for i,x in enumerate(shifted) if min(u,v)<i<max(u,v)]
                        if u>v:middle.reverse()
                        candidate=simplify([a,p,*middle,q,z])
                        if valid_shape(candidate) and paths_clear([candidate],[width],0):out.add(tuple(candidate))
    return [list(q) for q in sorted(out,key=lambda q:(len(q),sum(math.dist(u,v) for u,v in zip(q,q[1:])),q))[:limit]]


def safe_route(route,width,via_diameter):
    """Reject same-net self contacts that an ordinary DRC cannot diagnose."""
    from .bundle_geometry import paths_clear,_point_distance
    legs=route['legs'];vias=list(map(tuple,route['vias']))
    if not legs or len(vias)!=len(legs)-1:return False
    carrier=list(map(tuple,legs[0][1]))
    for i,((face,path),(next_face,next_path)) in enumerate(zip(legs,legs[1:])):
        if face==next_face or tuple(path[-1])!=tuple(next_path[0]) or tuple(path[-1])!=vias[i]:return False
        carrier.extend(map(tuple,next_path[1:]))
    if not paths_clear([simplify(carrier)],[width],0):return False
    for via in vias:
        for _,path in legs:
            for a,b in zip(path,path[1:]):
                if via in (tuple(a),tuple(b)):continue
                if _point_distance(via,a,b)+1e-6<(via_diameter+width)/2:return False
    return True
