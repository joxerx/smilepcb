"""Conservatively retain native-clear portions of a displaced straight track."""
from smilepcb.native_worker import p
from .local_router import xy,vec,distance


def retain_clear_fragments(track, oracle):
    """Return proposals only; every retained closed interval clears the oracle.

    Subdivide colliding intervals rather than deleting an entire long trunk.
    The 50 um stopping tolerance can remove extra copper, never retain an
    untested crossing. Vias/arcs are removed whole by the caller.
    """
    if track.GetClass()!='PCB_TRACK':return []
    start,end=xy(track.GetStart()),xy(track.GetEnd())
    layer,net,width=track.GetLayer(),track.GetNetname(),track.GetWidth()
    pending=[(start,end)];kept=[];visited=0
    while pending and visited<8192 and not oracle.expired():
        a,b=pending.pop();visited+=1
        if oracle.clear(p.SHAPE_SEGMENT(vec(a),vec(b),width),layer,net):
            if kept and kept[-1][1]==a:kept[-1]=(kept[-1][0],b)
            else:kept.append((a,b))
        elif distance(a,b)>p.FromMM(.05):
            middle=((a[0]+b[0])//2,(a[1]+b[1])//2)
            pending.extend(((middle,b),(a,middle)))
    result=[]
    for a,b in kept:
        if distance(a,b)<p.FromMM(.1):continue
        part=track.Duplicate();part.SetUuid(p.KIID())
        part.SetStart(vec(a));part.SetEnd(vec(b));result.append(part)
    return result
