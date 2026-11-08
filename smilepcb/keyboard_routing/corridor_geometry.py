"""Constant-normal-pitch offset lanes through shared octilinear turns."""
import math


def offset_path(points,pitch):
    if type(pitch) not in (int,float) or not math.isfinite(pitch) or not pitch:
        raise ValueError('A finite nonzero lane offset is required')
    points=[tuple(q) for q in points];clean=[]
    def direction(a,b):
        x,y=b[0]-a[0],b[1]-a[1];g=math.gcd(x,y)
        return (x//g,y//g) if g else (0,0)
    for point in points:
        if clean and point==clean[-1]:continue
        while len(clean)>1 and direction(clean[-2],clean[-1])==direction(clean[-1],point):clean.pop()
        clean.append(point)
    points=clean
    if len(points)<2 or points[0]==points[-1]:raise ValueError('An open lane spine is required')
    lines=[];directions=[]
    for a,b in zip(points,points[1:]):
        d=direction(a,b)
        if max(map(abs,d))!=1:raise ValueError('Lane spine must be octilinear')
        if directions:
            old=directions[-1];dot=sum(x*y for x,y in zip(old,d))
            if dot<=0 or 2*dot*dot<sum(x*x for x in old)*sum(x*x for x in d):
                raise ValueError('Lane spine has a reversal or sharp turn')
        directions.append(d);n=(-d[1],d[0]);delta=round(pitch*math.hypot(*n))
        lines.append((n,sum(n[i]*a[i] for i in (0,1))+delta,delta))
    def endpoint(point,line):
        n,_,delta=line;norm=sum(v*v for v in n);shift=[round(delta*v/norm) for v in n]
        error=delta-sum(n[i]*shift[i] for i in (0,1));i=next(i for i in (0,1) if n[i]);shift[i]+=error//n[i]
        return tuple(point[i]+shift[i] for i in (0,1))
    out=[endpoint(points[0],lines[0])]
    for (a,c,_),(b,d,_) in zip(lines,lines[1:]):
        det=a[0]*b[1]-a[1]*b[0]
        if not det:raise ValueError('Invalid shared lane turn')
        x,y=c*b[1]-a[1]*d,a[0]*d-c*b[0]
        if x%det or y%det:raise ValueError('Lane turn is off the native grid')
        out.append((x//det,y//det))
    out.append(endpoint(points[-1],lines[-1]))
    if any(direction(a,b)!=d for a,b,d in zip(out,out[1:],directions)):
        raise ValueError('Offset consumes a shared lane segment')
    return out


def turn_bundle(starts,heading,turn,*,run,chamfer,reach):
    """Expand a common spine into lanes at their actual normal offsets."""
    if heading not in ((1,0),(-1,0),(0,1),(0,-1)) or turn not in (-1,1):
        raise ValueError('Cardinal bank heading and signed turn required')
    if not starts or min(run,chamfer,reach)<=0 or reach<chamfer:
        raise ValueError('Positive bounded corridor dimensions required')
    tangent=(-heading[1],heading[0]);origin=min(map(tuple,starts),key=lambda q:sum(q[i]*tangent[i] for i in (0,1)))
    offsets=[sum((q[i]-origin[i])*tangent[i] for i in (0,1)) for q in starts]
    if any(sum((q[i]-origin[i])*heading[i] for i in (0,1))!=0 for q in starts):
        raise ValueError('Bundle must start at one actual launch rail')
    point=lambda normal,t:tuple(origin[i]+heading[i]*normal+tangent[i]*turn*t for i in (0,1))
    spine=[origin,point(run,0),point(run+chamfer,chamfer),point(run+chamfer,reach)]
    return [spine if not offset else offset_path(spine,offset) for offset in offsets]
