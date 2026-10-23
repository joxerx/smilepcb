"""Bounded 45-degree skew tuning on explicitly owned new pair segments."""
import math

from .native_worker import p
from .routing_style import Clearance, add_path, filled_zones, vec, xy


def balance_pair(board, nets, *, editable_ids):
    """Keep the existing <20 mm / <0.5 mm recipe; aim for <0.25 mm on repair.

    This is geometric matching, not an impedance or signal-integrity proof.
    No source segment or interior copper contact may be lost. The caller must
    still run native ERC/DRC and independent saved-board connectivity checks.
    """
    if len(nets)!=2 or len(set(nets))!=2:raise ValueError('Select two distinct pair nets')
    by_id={t.m_Uuid.AsString():t for t in board.GetTracks()}
    editable=set(editable_ids)
    if editable-by_id.keys():raise ValueError('Unknown editable pair segment')
    tracks={net:[t for t in by_id.values() if t.GetNetname()==net] for net in nets}
    selected=[t for group in tracks.values() for t in group]
    if (not selected or any(not tracks[net] for net in nets) or
            any(t.GetClass()!='PCB_TRACK' for t in selected) or len({t.GetLayer() for t in selected})!=1):
        raise ValueError('Pair must have existing single-layer segments without vias')
    lengths={net:sum(t.GetLength() for t in group) for net,group in tracks.items()}
    if not all(0<value<20000000 for value in lengths.values()):raise ValueError('Pair exceeds the recipe length envelope')
    report={'changed':False,'lengths_mm':{net:value/1e6 for net,value in lengths.items()},'added_ids':[],'removed_ids':[]}
    if abs(lengths[nets[0]]-lengths[nets[1]])<500000:return report
    short=min(nets,key=lambda net:lengths[net]);long=max(lengths.values());oracle=Clearance(board)
    candidates=[]
    for t in tracks[short]:
        uid=t.m_Uuid.AsString()
        if uid not in editable or t.IsLocked():continue
        a,b=xy(t.GetStart()),xy(t.GetEnd());dx,dy=b[0]-a[0],b[1]-a[1]
        if (dx and dy and abs(dx)!=abs(dy)) or a==b:continue
        u=(0 if not dx else (1 if dx>0 else -1),0 if not dy else (1 if dy>0 else -1))
        span=max(abs(dx),abs(dy));old_length=t.GetLength()
        for offset in range(125000,2000001,125000):
            for fraction in (.2,.35,.1):
                inset=round(span*fraction)
                if span-2*inset-2*offset<500000:continue
                for side in (-1,1):
                    n=(-u[1]*side,u[0]*side)
                    q1=tuple(a[i]+u[i]*inset for i in (0,1))
                    q2=tuple(q1[i]+(u[i]+n[i])*offset for i in (0,1))
                    q4=tuple(b[i]-u[i]*inset for i in (0,1))
                    q3=tuple(q4[i]-u[i]*offset+n[i]*offset for i in (0,1))
                    path=[a,q1,q2,q3,q4,b]
                    length=lengths[short]-old_length+sum(math.dist(x,y) for x,y in zip(path,path[1:]))
                    if length<20000000 and abs(long-length)<250000:
                        candidates.append((abs(long-length),uid,path,t))
    for _,uid,path,t in sorted(candidates,key=lambda row:(row[0],row[1],row[2])):
        layer=t.GetLayer();width=t.GetWidth()
        if not all(oracle.clear(a,b,width,layer,short) for a,b in zip(path,path[1:])):continue
        old=t.GetEffectiveShape(layer)
        shapes=[p.SHAPE_SEGMENT(vec(a),vec(b),width) for a,b in zip(path,path[1:])]
        contacts=[item.GetEffectiveShape(layer) for item in [*board.GetTracks(),*(d for f in board.GetFootprints() for d in f.Pads())]
                  if item.m_Uuid.AsString()!=uid and item.GetNetname()==short and item.IsOnLayer(layer)]
        contacts.extend(shape for shape,net,face,_ in filled_zones(board) if net==short and face==layer)
        if any(p.SHAPE.Collide(old,shape,0) and not any(p.SHAPE.Collide(new,shape,0) for new in shapes) for shape in contacts):continue
        board.Remove(t)
        added=add_path(board,oracle,path,layer,short,width);board.BuildConnectivity()
        lengths[short]=sum(t.GetLength() for t in board.GetTracks() if t.GetNetname()==short)
        return dict(report,changed=True,net=short,added_ids=added,removed_ids=[uid],
                    lengths_mm={net:value/1e6 for net,value in lengths.items()})
    raise ValueError('No clearance/contact-safe skew tuning within the bounded search')
