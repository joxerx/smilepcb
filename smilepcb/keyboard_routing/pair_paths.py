"""Bounded coupled octile candidates for aligned two-lane terminals."""


def paired_octile_paths(starts,ends):
    """Slide a shared 45-degree turn without choosing a board-specific corridor.

    Cardinal terminal banks must have the same lane ordering. This is a
    candidate generator only; native collision and pair-length checks decide
    admission. Boards with incompatible banks use the existing routing path.
    """
    a,z=starts;c,d=ends;dx,dy=z[0]-a[0],z[1]-a[1]
    if bool(dx)==bool(dy) or all(s==t for s,t in zip(starts,ends)):return
    v=(0,1 if dy>0 else -1) if dy else (1 if dx>0 else -1,0)
    ex,ey=d[0]-c[0],d[1]-c[1]
    if dx*ey!=dy*ex or dx*ex+dy*ey<=0:return
    u=(v[1],-v[0]);delta=(c[0]+d[0]-a[0]-z[0],c[1]+d[1]-a[1]-z[1])
    if sum(u[i]*delta[i] for i in (0,1))<0:u=tuple(-q for q in u)
    local=lambda p:(sum(p[i]*u[i] for i in (0,1)),sum(p[i]*v[i] for i in (0,1)))
    world=lambda p:(p[0]*u[0]+p[1]*v[0],p[0]*u[1]+p[1]*v[1])
    aa=list(map(local,starts));bb=list(map(local,ends))
    low=max(p[0] for p in aa);high=min(t[0]-abs(t[1]-s[1]) for s,t in zip(aa,bb))
    if high<low:return
    step=max(125000,(high-low+254)//255)
    for bend in sorted(set(range(low,high+1,step))|{high}):
        paths=[]
        for start,end in zip(aa,bb):
            points=[start,(bend,start[1]),(bend+abs(end[1]-start[1]),end[1]),end]
            paths.append([world(q) for i,q in enumerate(points) if not i or q!=points[i-1]])
        yield paths
