"""Shorten an exclusive SMD-to-via spoke without changing the circuit.

Only editable leaf routing is replaced. Planning is nonmutating; the caller
owns pour refill, complete pad partitions, file transactions and native DRC.
"""
import math
import time
from smilepcb.native_worker import p
from smilepcb.keyboard_routing.local_router import LocalRouter,xy,vec,distance
from smilepcb.keyboard_routing.grid_search import valid_plan
from smilepcb.routing_style import octile_paths


def spoke(board,via,layer,protected,deadline):
    """Return a whole layer component ending at exactly one SMD pad."""
    if (via.IsLocked() or via.m_Uuid.AsString() in protected or not via.GetNetname()
            or via.GetViaType()!=p.VIATYPE_THROUGH):return None
    net=via.GetNetname();items=[t for t in board.GetTracks() if not isinstance(t,p.PCB_VIA) and t.IsOnLayer(layer) and t.GetNetname()==net]
    items += [d for f in board.GetFootprints() for d in f.Pads() if d.IsOnLayer(layer) and d.GetNetname()==net]
    shapes=[t.GetEffectiveShape(layer) for t in items];annulus=via.GetEffectiveShape(layer)
    found={i for i,s in enumerate(shapes) if p.SHAPE.Collide(s,annulus,0)};todo=list(found)
    while todo:
        if time.monotonic()>=deadline or len(found)>64:return None
        i=todo.pop()
        for j,s in enumerate(shapes):
            if j not in found and shapes[i].BBox().Intersects(s.BBox()) and p.SHAPE.Collide(shapes[i],s,0):found.add(j);todo.append(j)
    pads=[items[i] for i in found if isinstance(items[i],p.PAD)]
    tracks=[items[i] for i in found if isinstance(items[i],p.PCB_TRACK)]
    if len(pads)!=1 or pads[0].GetAttribute()!=p.PAD_ATTRIB_SMD or not tracks or len(tracks)>32:return None
    if any(t.GetClass()!='PCB_TRACK' or t.IsLocked() or t.m_Uuid.AsString() in protected for t in tracks):return None
    for v in board.GetTracks():
        if not isinstance(v,p.PCB_VIA) or v.m_Uuid==via.m_Uuid or v.GetNetname()!=net:continue
        if any(p.SHAPE.Collide(v.GetEffectiveShape(layer),shapes[i],0) for i in found):return None
    return pads[0],tracks


def rehome_spoke(board,via,layer,*,protected,deadline):
    if board.GetCopperLayerCount()!=2 or layer not in (p.F_Cu,p.B_Cu):raise ValueError('Expected two-layer routing')
    branch=spoke(board,via,layer,set(protected),deadline)
    if branch is None:return None
    pad,tracks=branch;old=xy(via.GetPosition());a=xy(pad.GetPosition());net=via.GetNetname()
    old_length=sum(t.GetLength() for t in tracks)
    if old_length<p.FromMM(1):return None
    other=p.B_Cu if layer==p.F_Cu else p.F_Cu
    contacts=[t for t in [*board.GetTracks(),*(d for f in board.GetFootprints() for d in f.Pads())]
              if t.m_Uuid!=via.m_Uuid and t.IsOnLayer(other) and t.GetNetname()==net
              and p.SHAPE.Collide(t.GetEffectiveShape(other),via.GetEffectiveShape(other),0)]
    if not contacts:return None
    # The generic endpoint projector handles straight tracks, pads and vias;
    # projecting an arc onto its chord would not preserve its actual contact.
    if any(isinstance(t,p.PCB_ARC) for t in contacts):return None
    removed={t.m_Uuid.AsString() for t in tracks}|{via.m_Uuid.AsString()}
    width=max(t.GetWidth() for t in tracks)
    router=LocalRouter(board,deadline=deadline,compact=True,ignored_copper=removed,track_width_nm=width)
    # Retain the source via's manufacturing/current profile, even when it
    # exceeds the board-wide minimum used by ordinary new signal vias.
    diameter=via.GetWidth(p.F_Cu)
    if diameter!=via.GetWidth(p.B_Cu):return None
    if diameter<router.via_width or via.GetDrillValue()<router.drill:return None
    router.via_width=diameter;router.drill=via.GetDrillValue()
    box=pad.GetEffectiveShape(layer).BBox();axis=0 if box.GetWidth()>=box.GetHeight() else 1
    def leg(x,z,face,terminal=False):
        choices=list(octile_paths(x,z))
        directions=([(1,0),(-1,0)] if axis==0 else [(0,1),(0,-1)]) if terminal else [(1,0),(-1,0),(0,1),(0,-1)]
        for radius in (500000,1000000,1500000,2000000):
            for dx,dy in directions:
                lead=(x[0]+dx*radius,x[1]+dy*radius)
                choices.extend([x]+path for path in octile_paths(lead,z))
        choices.sort(key=lambda path:sum(distance(u,v) for u,v in zip(path,path[1:])))
        for path in choices:
            if router.expired():return None
            if all(router.clear(p.SHAPE_SEGMENT(vec(u),vec(v),width),face,net) for u,v in zip(path,path[1:])):return path
        return None
    stubs=[]
    for item in contacts:
        point,_=router.endpoints(item,old);path=leg(old,point,other)
        if path is None:return None
        stubs.append((other,path))
    candidates={tuple(round(a[i]+radius*(math.cos(angle*math.pi/8) if i==0 else math.sin(angle*math.pi/8))) for i in (0,1))
                for radius in range(750000,4000001,250000) for angle in range(16)}
    for v in sorted(candidates,key=lambda q:(distance(a,q),q)):
        if router.expired():return None
        if not router.via_clear(v,net):continue
        front=leg(a,v,layer,True)
        if front is None:continue
        length=sum(distance(x,z) for x,z in zip(front,front[1:]))
        if length>=old_length-p.FromMM(.5):continue
        back=leg(old,v,other)
        if back is None:continue
        legs=stubs+[(layer,front),(other,back)]
        if not valid_plan(router,legs,[v],net):continue
        if router.expired() or time.monotonic()>=deadline:return None
        # Commit only after every replacement lead and the new drill pass.
        for t in tracks:board.Delete(t)
        board.Delete(via)
        for face,path in legs:router.add_path(path,face,net)
        router.add_via(v,net)
        return {'net':net,'pad':[pad.GetParentFootprint().GetReference(),pad.GetNumber()],
                'old_via_nm':list(old),'new_via_nm':list(v),'removed_ids':sorted(removed),
                'added_ids':router.added,'spoke_length_before_nm':old_length,'spoke_length_after_nm':length}
    return None
