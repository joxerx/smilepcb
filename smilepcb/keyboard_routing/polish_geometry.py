"""Generic fixed-terminal chain rewrites for a native-checked routing optimization."""
from collections import defaultdict
import math
from .follow_geometry import simplify,direction


def chains(rows,anchors=()):
    groups=defaultdict(dict);out=[];anchors=set(anchors)
    for uid,r in rows.items():
        a,b=tuple(r['start']),tuple(r['end'])
        if a!=b:groups[r['net'],r['layer'],r['width']][uid]=(a,b)
    for (net,face,width),edges in sorted(groups.items()):
        adj=defaultdict(list);seen=set()
        for uid,(a,b) in edges.items():adj[a].append((uid,b));adj[b].append((uid,a))
        stop=lambda q:len(adj[q])!=2 or (net,face,q) in anchors
        for start in sorted(adj,key=lambda q:(not stop(q),q)):
            for uid,end in sorted(adj[start]):
                if uid in seen:continue
                seen.add(uid);ids=[uid];path=[start,end]
                while not stop(path[-1]):
                    choices=[x for x in adj[path[-1]] if x[0] not in seen]
                    if not choices:break
                    uid,end=choices[0];seen.add(uid);ids.append(uid);path.append(end)
                if path[0]!=path[-1]:out.append(dict(net=net,layer=face,width=width,path=simplify(path),ids=ids))
    return out


def octile(a,b):
    dx,dy=b[0]-a[0],b[1]-a[1];m=min(abs(dx),abs(dy));sx=1 if dx>=0 else -1;sy=1 if dy>=0 else -1
    for mid in [(a[0]+sx*m,a[1]+sy*m),(b[0]-sx*m,b[1]-sy*m)]:yield simplify([a,mid,b])


def valid_shape(path):
    previous=None
    for a,b in zip(path,path[1:]):
        d=direction(a,b)
        if max(map(abs,d))!=1:return False
        if previous is not None:
            dot=sum(a*b for a,b in zip(previous,d))
            if dot<=0 or 2*dot*dot<sum(x*x for x in previous)*sum(x*x for x in d):return False
        previous=d
    return len(path)>1


def shape_score(path,bend_price):
    path=simplify(path)
    return sum(math.dist(a,b) for a,b in zip(path,path[1:]))+max(0,len(path)-2)*bend_price


def shortcut_candidates(path):
    path=simplify(path);out={tuple(path)}
    for i in range(len(path)-1):
        for j in range(i+2,len(path)):
            for middle in octile(path[i],path[j]):
                p=simplify([*path[:i],*middle,*path[j+1:]])
                if valid_shape(p):out.add(tuple(p))
    return [list(p) for p in sorted(out)]
