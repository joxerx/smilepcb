"""Finite shared spines from real obstacle events and terminal headings.

These are geometric proposals for a bundle, independent of signal names or
lane count. Actual offsets, clearances, contact and Native admission belong to
the producer. No obstacle bounding box is treated as an electrical proof.
"""
import math
import time
from .follow_geometry import direction,simplify
from .polish_geometry import octile,valid_shape
from .bundle_geometry import paths_clear


def common_rail_spines(start,end,*,source_heading,arrival_heading,bounds_nm,
                       obstacle_bounds_nm=(),envelope_radius_nm,lead_nm=None,
                       target_lead_nm=None,max_candidates=128,deadline=None):
    """Offer simple octile corridors with one long cardinal body.

    Work in the arrival's physical frame, anchored at the exact source, so
    translation and cardinal transforms do not change the sampling phase.
    Backward arrivals need another topology and yield no proposals here.
    """
    def point(q):
        if len(q)!=2 or any(type(v) is not int for v in q):raise ValueError('Integer nm points required')
        return tuple(q)
    def box(q):
        if len(q)!=4 or any(type(v) is not int for v in q) or q[0]>=q[2] or q[1]>=q[3]:raise ValueError('Positive integer nm bounds required')
        return tuple(q)
    start,end=point(start),point(end);sh,ah=point(source_heading),point(arrival_heading)
    if max(map(abs,sh))!=1 or sum(v*v for v in ah)!=1:raise ValueError('Octile departure and cardinal arrival required')
    if type(envelope_radius_nm) is not int or envelope_radius_nm<=0:raise ValueError('Positive physical bundle envelope required')
    if type(max_candidates) is not int or not 1<=max_candidates<=256:raise ValueError('Candidate limit must be 1..256')
    if deadline is not None and not math.isfinite(deadline):raise ValueError('Finite absolute deadline required')
    if deadline is not None and time.monotonic()>=deadline:return []
    radius=envelope_radius_nm
    lead=radius if lead_nm is None else lead_nm
    target_lead=2*radius if target_lead_nm is None else target_lead_nm
    if any(type(q) is not int or q<=0 for q in (lead,target_lead)):raise ValueError('Positive integer terminal leads required')
    u=ah;v=(-ah[1],ah[0])
    def local(q):
        d=tuple(q[i]-start[i] for i in (0,1));return (sum(d[i]*u[i] for i in (0,1)),sum(d[i]*v[i] for i in (0,1)))
    def world(q):return tuple(start[i]+u[i]*q[0]+v[i]*q[1] for i in (0,1))
    def local_box(q):
        q=box(q);points=[local((x,y)) for x in (q[0],q[2]) for y in (q[1],q[3])]
        return (min(x for x,y in points),min(y for x,y in points),max(x for x,y in points),max(y for x,y in points))
    board=local_box(bounds_nm);obstacles=[local_box(q) for q in obstacle_bounds_nm]
    target=local(end);h=(sum(sh[i]*u[i] for i in (0,1)),sum(sh[i]*v[i] for i in (0,1)))
    if target[0]<=0:return []
    launch=(h[0]*lead,h[1]*lead);arrival=(target[0]-target_lead,target[1])
    rails={board[1]+radius,board[3]-radius}
    for sign in (-1,1):
        rails.update(launch[1]+sign*k*radius for k in (2,3))
    for low_x,low_y,high_x,high_y in obstacles:rails.update((low_y-radius,high_y+radius))
    proposals=set()
    for rail in sorted(rails):
        if deadline is not None and time.monotonic()>=deadline:break
        if not board[1]+radius<=rail<=board[3]-radius:continue
        body_start=(launch[0]+radius,rail)
        body_end=(arrival[0]-abs(arrival[1]-rail),rail)
        if body_end[0]-body_start[0]<radius:continue
        for prefix in octile(launch,body_start):
            for suffix in octile(body_end,arrival):
                path=simplify([(0,0),*prefix,body_end,*suffix[1:],target])
                if (not valid_shape(path) or direction(*path[:2])!=h or direction(*path[-2:])!=(1,0)
                        or any(not board[0]<=x<=board[2] or not board[1]<=y<=board[3] for x,y in path)
                        or not paths_clear([path],[0],0)):continue
                proposals.add(tuple(world(q) for q in path))
    def quality(path):
        lengths=[math.dist(a,z) for a,z in zip(path,path[1:])]
        return (len(path),-max(lengths),sum(lengths),path)
    return [list(path) for path in sorted(proposals,key=quality)[:max_candidates]]


def terminal_heading(points,body_center,*,departure):
    """Infer an octile bank normal, outward at a source or inward at a target."""
    points=[tuple(q) for q in points];center=tuple(body_center)
    if type(departure) is not bool or len(center)!=2 or any(type(v) is not int for v in center):raise ValueError('Physical body centre and direction required')
    if any(len(q)!=2 or any(type(v) is not int for v in q) for q in points):raise ValueError('Integer nm bank points required')
    if len(points)<2:return None
    a=points[0];other=next((q for q in points if q!=a),None)
    if other is None:return None
    d=direction(a,other)
    if max(map(abs,d))!=1 or any((q[0]-a[0])*d[1]!=(q[1]-a[1])*d[0] for q in points):return None
    normal=(-d[1],d[0]);delta=[sum(q[i] for q in points)-len(points)*center[i] for i in (0,1)]
    if not departure:delta=[-q for q in delta]
    return max((normal,tuple(-q for q in normal)),key=lambda h:(sum(h[i]*delta[i] for i in (0,1)),h))
