"""Finite candidates offset from real foreign copper; no planned bundle axis."""
from collections import defaultdict
import math

def direction(a,b):
    x,y=b[0]-a[0],b[1]-a[1];g=math.gcd(x,y)
    return (x//g,y//g) if g else (0,0)

def simplify(points):
    out=[]
    for p in map(tuple,points):
        if out and p==out[-1]:continue
        while len(out)>1 and direction(out[-2],out[-1])==direction(out[-1],p):out.pop()
        out.append(p)
    return out

def canonical_edges(edges):
    carriers=defaultdict(list);other=set();points={p for edge in edges for p in edge}
    for a,b in edges:
        d=direction(a,b)
        if d[0]<0 or (not d[0] and d[1]<0):d=(-d[0],-d[1])
        if max(map(abs,d))!=1:other.add((a,b));continue
        n=(-d[1],d[0]);c=sum(n[i]*a[i] for i in (0,1));u=sum(d[i]*a[i] for i in (0,1));v=sum(d[i]*b[i] for i in (0,1))
        carriers[d,c].append((min(u,v),max(u,v)))
    for (d,c),intervals in carriers.items():
        merged=[]
        for a,b in sorted(intervals):
            if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],b))
            else:merged.append((a,b))
        norm=d[0]**2+d[1]**2;n=(-d[1],d[0])
        for a,b in merged:
            cuts=sorted({a,b}|{sum(d[i]*p[i] for i in (0,1)) for p in points if sum(n[i]*p[i] for i in (0,1))==c and a<sum(d[i]*p[i] for i in (0,1))<b})
            point=lambda u:((d[0]*u-d[1]*c)//norm,(d[1]*u+d[0]*c)//norm)
            other.update(tuple(sorted((point(u),point(v)))) for u,v in zip(cuts,cuts[1:]))
    return other

def foreign_chains(segments,query_net,eligible):
    groups=defaultdict(set)
    for s in segments:
        if s['net']==query_net or s['net'] not in eligible:continue
        a,b=map(tuple,(s['start'],s['end']))
        if a==b:continue
        groups[(s['net'],s['layer'],s['width'])].add(tuple(sorted((a,b))))
    out=[]
    for (net,layer,width),edges in sorted(groups.items()):
        edges=canonical_edges(edges)
        incident=defaultdict(list)
        for a,b in sorted(edges):incident[a].append(b);incident[b].append(a)
        seen=set()
        for start in sorted(incident,key=lambda p:(len(incident[p])==2,p)):
            for following in sorted(incident[start]):
                edge=tuple(sorted((start,following)))
                if edge in seen:continue
                seen.add(edge);points=[start,following]
                while len(points)<129 and len(incident[points[-1]])==2:
                    nxt=next(q for q in incident[points[-1]] if q!=points[-2]);key=tuple(sorted((points[-1],nxt)))
                    if key in seen:break
                    seen.add(key);points.append(nxt)
                points=simplify(points);points=min(points,list(reversed(points)))
                out.append(dict(net=net,layer=layer,width=width,path=points))
    return sorted(out,key=lambda c:(c['net'],c['layer'],c['width'],c['path']))

def offset_path(points,pitch):
    if not math.isfinite(pitch) or not pitch:return None
    points=simplify(points)
    if len(points)<2 or points[0]==points[-1]:return None
    lines=[];directions=[]
    for a,b in zip(points,points[1:]):
        d=direction(a,b)
        if max(map(abs,d))!=1:return None
        if directions:
            prev=directions[-1];dot=prev[0]*d[0]+prev[1]*d[1]
            if dot<=0 or 2*dot*dot<sum(x*x for x in prev)*sum(x*x for x in d):return None
        directions.append(d);n=(-d[1],d[0]);delta=round(pitch*math.hypot(*n));c=n[0]*a[0]+n[1]*a[1]+delta
        lines.append((n,c,delta))
    def end(point,line):
        n,_,delta=line;shift=[round(delta*x/(n[0]**2+n[1]**2)) for x in n]
        error=delta-sum(n[i]*shift[i] for i in (0,1));i=next(i for i in (0,1) if n[i]);shift[i]+=error//n[i]
        return tuple(point[i]+shift[i] for i in (0,1))
    out=[end(points[0],lines[0])]
    for (a,c,_),(b,d,_) in zip(lines,lines[1:]):
        det=a[0]*b[1]-a[1]*b[0]
        if not det:return None
        x,y=c*b[1]-a[1]*d,a[0]*d-c*b[0]
        if x%det or y%det:return None
        out.append((x//det,y//det))
    out.append(end(points[-1],lines[-1]));out=simplify(out)
    if len(out)!=len(points) or any(direction(a,b)!=d for a,b,d in zip(out,out[1:],directions)):return None
    return out

def projections(points,query,limit=2):
    out=[]
    for i,(a,b) in enumerate(zip(points,points[1:])):
        d=direction(a,b);steps=math.gcd(b[0]-a[0],b[1]-a[1]);norm=d[0]**2+d[1]**2
        t=max(0,min(steps,round(sum((query[j]-a[j])*d[j] for j in (0,1))/norm)))
        q=tuple(a[j]+t*d[j] for j in (0,1));out.append((math.dist(q,query),i+t/steps,q))
    return sorted(out)[:limit]

def follow_spans(points,a,z):
    out=set()
    for _,u,p in projections(points,a):
        for _,v,q in projections(points,z):
            if u==v:continue
            middle=[tuple(x) for i,x in enumerate(points) if min(u,v)<i<max(u,v)]
            if u>v:middle.reverse()
            path=simplify([p,*middle,q])
            if sum(math.dist(x,y) for x,y in zip(path,path[1:]))>=1000000:out.add(tuple(path))
    return [list(p) for p in sorted(out)]
