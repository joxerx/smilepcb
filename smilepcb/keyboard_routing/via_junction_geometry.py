"""Geometry-derived sites and simple arms for moving a complete via junction."""
import math,time
from .follow_geometry import simplify
from .polish_geometry import octile,valid_shape,shape_score


def arm_options(path,destination):
 """Retain the fixed outer anchor; replace a suffix rather than bridge old via."""
 path=simplify(path);destination=tuple(destination);out=set()
 for i in sorted({0,*range(max(0,len(path)-5),len(path)-1)}):
  for tail in octile(path[i],destination):
   q=simplify([*path[:i],*tail])
   if valid_shape(q) and q[0]==path[0] and q[-1]==destination:out.add(tuple(q))
 return [list(q) for q in sorted(out,key=lambda q:(shape_score(q,2000000),q))]


def junction_sites(arms,origin,*,limit=96,deadline=None,guide_points=()):
 """Intersect octile carrier lines through real anchors/bends, without a grid."""
 if type(limit) is not int or not 1<=limit<=1024:raise ValueError('Positive bounded site count required')
 expired=lambda:deadline is not None and time.monotonic()>=deadline
 if expired():return []
 origin=tuple(origin);points={origin,*map(tuple,guide_points)}
 for arm in arms:points.update(map(tuple,[arm[0],*arm[-4:]]))
 if len(points)>192:raise ValueError('Too many junction guide points')
 # n.x = c: horizontal, vertical, and the two 45-degree families.
 normals=((1,0),(0,1),(1,1),(1,-1));lines=[(n,c) for n in normals for c in sorted({n[0]*x+n[1]*y for x,y in points})]
 low=[min(p[i] for p in points) for i in (0,1)];high=[max(p[i] for p in points) for i in (0,1)];sites=set(points)
 for j,(a,c) in enumerate(lines):
  if expired():break
  for k,(b,d) in enumerate(lines[:j]):
   if k%64==0 and expired():break
   det=a[0]*b[1]-a[1]*b[0]
   if not det:continue
   x,y=c*b[1]-a[1]*d,a[0]*d-c*b[0]
   if x%det or y%det:continue
   q=(x//det,y//det)
   if all(low[i]<=q[i]<=high[i] for i in (0,1)):sites.add(q)
 sites.discard(origin)
 # Distance is only an enumeration heuristic. Native physical and full route
 # quality checks decide admission, never this optimistic lower bound.
 anchors=[tuple(a[0]) for a in arms]
 return sorted(sites,key=lambda q:(sum(max(abs(q[i]-a[i]) for i in (0,1))+(math.sqrt(2)-1)*min(abs(q[i]-a[i]) for i in (0,1)) for a in anchors),math.dist(q,origin),q))[:limit]
