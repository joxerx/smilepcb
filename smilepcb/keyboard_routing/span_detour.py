"""Geometry-derived same-face octile detours for an editable simple span."""
import math
from smilepcb.native_worker import p
from smilepcb.keyboard_routing.local_router import LocalRouter,xy,vec,distance
from smilepcb.routing_style import octile_paths


def proposals(board,track,*,protected,deadline):
    uid=track.m_Uuid.AsString()
    if track.GetClass()!='PCB_TRACK' or track.IsLocked() or uid in protected:return []
    a,z=xy(track.GetStart()),xy(track.GetEnd());net=track.GetNetname();face=track.GetLayer();width=track.GetWidth()
    if not net or a==z:return []
    shape=track.GetEffectiveShape(face);contacts=[];ends=[p.SHAPE_CIRCLE(vec(v),1000) for v in (a,z)]
    for item in [*board.GetTracks(),*(d for f in board.GetFootprints() for d in f.Pads())]:
        if item.m_Uuid==track.m_Uuid or not item.IsOnLayer(face) or item.GetNetname()!=net:continue
        contact=item.GetEffectiveShape(face)
        if not p.SHAPE.Collide(shape,contact,0):continue
        if not any(p.SHAPE.Collide(c,contact,0) for c in ends):return []
        contacts.append(contact)
    router=LocalRouter(board,deadline=deadline,compact=True,ignored_copper={uid},track_width_nm=width)
    dx,dy=z[0]-a[0],z[1]-a[1];length=distance(a,z);midpoints=set()
    for fraction in (.25,.5,.75):
        midpoints.update(((round(a[0]+dx*fraction),a[1]),(a[0],round(a[1]+dy*fraction)),
                         (round(z[0]-dx*fraction),z[1]),(z[0],round(z[1]-dy*fraction))))
    for offset in (.25,.5,1):
        for sign in (-1,1):midpoints.add((round((a[0]+z[0])/2-sign*dy*offset),round((a[1]+z[1])/2+sign*dx*offset)))
    found={}
    for middle in sorted(midpoints):
        for left in octile_paths(a,middle):
            for right in octile_paths(middle,z):
                if router.expired():return list(found.values())
                raw=left+right[1:];path=[]
                for q in raw:
                    if path and q==path[-1]:continue
                    if len(path)>=2 and (path[-1][0]-path[-2][0])*(q[1]-path[-1][1])==(path[-1][1]-path[-2][1])*(q[0]-path[-1][0]):path.pop()
                    path.append(q)
                if len(path)<3:continue
                legs=[p.SHAPE_SEGMENT(vec(u),vec(v),width) for u,v in zip(path,path[1:])]
                if not all(router.clear(s,face,net) for s in legs):continue
                if any(not any(p.SHAPE.Collide(c,s,0) for s in legs) for c in contacts):continue
                key=tuple(path);found[key]={'removed_id':uid,'net':net,'layer':face,'width':width,'path':path,'length_nm':sum(distance(u,v) for u,v in zip(path,path[1:]))}
    return sorted(found.values(),key=lambda c:(c['length_nm'],c['path']))


def apply(board,proposal):
    old=next(t for t in board.GetTracks() if t.m_Uuid.AsString()==proposal['removed_id'])
    board.Delete(old);router=LocalRouter(board,compact=True,track_width_nm=proposal['width'])
    router.add_path(proposal['path'],proposal['layer'],proposal['net']);return router.added
