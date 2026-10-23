"""Editable via/spoke plans used inside a checked completion transaction."""
import math
import time
from smilepcb.native_worker import p
from .local_router import LocalRouter,xy
from .native_graph import route_components
from .via_spoke import spoke,rehome_spoke
from .via_escape import detach_vias,move_via


def short_spokes(board,protected,deadline):
    mcu=next((f for f in board.GetFootprints() if f.GetReference()=='U_MCU'),None)
    if mcu is None:return {'moves':[],'removed_ids':[]}
    face=mcu.GetLayer();body=mcu.GetCourtyard(face);near=body.BBox();near.Inflate(p.FromMM(2));candidates=[]
    for via in board.GetTracks():
        if time.monotonic()>=deadline:break
        if not isinstance(via,p.PCB_VIA) or not near.Contains(via.GetPosition()):continue
        part=spoke(board,via,face,protected,deadline)
        if part is None:continue
        pad,tracks=part
        if not any(p.SHAPE.Collide(t.GetEffectiveShape(face),body,0) for t in tracks):continue
        length=sum(t.GetLength() for t in tracks)
        if length>=p.FromMM(2):candidates.append((-length,pad.GetNumber(),via))
    moves=[]
    for _,_,via in sorted(candidates,key=lambda row:(row[0],row[1]))[:6]:
        if time.monotonic()>=deadline:break
        move=rehome_spoke(board,via,face,protected=protected,deadline=min(deadline,time.monotonic()+6))
        if move is not None:moves.append(move)
    return {'moves':moves,'removed_ids':[uid for move in moves for uid in move['removed_ids']]}


def shift_blocking_vias(board,net,protected,hard,deadline):
    vias={v.m_Uuid.AsString():v for v in board.GetTracks() if isinstance(v,p.PCB_VIA)}
    soft={uid for uid,v in vias.items() if uid not in protected and uid not in hard and not v.IsLocked() and v.GetNetname()!=net}
    world=LocalRouter(board,deadline=deadline,compact=True)
    relaxed=LocalRouter(board,deadline=min(deadline,time.monotonic()+15),compact=True,ignored_copper=soft,
                        track_width_nm=board.GetDesignSettings().m_TrackMinWidth)
    relaxed.grid_step=125000;relaxed.heuristic_weight=3;relaxed.connection_budget_s=6
    relaxed.routing_penalty=lambda shape,layer,name,via:p.FromMM(10)*len(set(world.collisions(shape,layer,name,via))&soft)
    joined=route_components(board,relaxed,{net});blockers=set()
    for track in board.GetTracks():
        if track.m_Uuid.AsString() not in relaxed.added:continue
        for layer in (p.F_Cu,p.B_Cu):
            if track.IsOnLayer(layer):blockers.update(world.collisions(track.GetEffectiveShape(layer),layer,net,isinstance(track,p.PCB_VIA)))
    if not blockers<=soft:raise ValueError('Via shift touched a hard routing obstacle')
    cluster=detach_vias(board,[vias[uid] for uid in sorted(blockers)]);moves=[];failed=[]
    for uid in sorted(blockers):
        via=cluster[uid];board.Add(via);old=xy(via.GetPosition());change=None
        oracle=LocalRouter(board,deadline=deadline,compact=True,ignored_copper={uid})
        for radius in range(100000,2000001,100000):
            if time.monotonic()>=deadline or change is not None:break
            for angle in range(16):
                if time.monotonic()>=deadline:break
                point=(old[0]+round(radius*math.cos(angle*math.pi/8)),old[1]+round(radius*math.sin(angle*math.pi/8)))
                change=move_via(board,via,point,protected=protected,deadline=deadline,oracle=oracle)
                if change is not None:moves.append(change);break
        if change is None:failed.append(uid);break
    return {'planned_connections':joined,'moves':moves,'failed_vias':failed,'added_ids':relaxed.added,
            'blocking_vias':len(blockers),'search':getattr(relaxed,'graph_diagnostic',{})}
