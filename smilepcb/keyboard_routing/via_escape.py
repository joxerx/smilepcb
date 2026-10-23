"""Local via displacement; caller owns transaction and acceptance."""
import time
from smilepcb.native_worker import p
from smilepcb.keyboard_routing.local_router import LocalRouter,xy,vec



def detach_vias(board,vias):
    """Detach a transient collision cluster without changing object identity."""
    copied={v.m_Uuid.AsString():v.Duplicate() for v in vias}
    for uid,copy in copied.items():copy.SetUuid(p.KIID(uid))
    for v in vias:board.Delete(v)
    return copied


def move_via(board,via,destination,*,protected=(),deadline=None,oracle=None):
    """Move one unlocked via while retaining all explicit copper contacts.

    This is a bounded probe, not a substitute for refill, pad-partition
    verification and complete native DRC. Failure leaves the board unchanged.
    An oracle ignoring this via can be reused across unchanged failed trials;
    discard it immediately after a successful move or any other board edit.
    """
    uid=via.m_Uuid.AsString()
    if via.IsLocked() or uid in protected or (deadline is not None and time.monotonic()>=deadline):return None
    if not isinstance(via,p.PCB_VIA) or via.GetViaType()!=p.VIATYPE_THROUGH:
        raise ValueError('Expected an ordinary through-via')
    old=xy(via.GetPosition());net=via.GetNetname()
    if oracle is None:oracle=LocalRouter(board,deadline=deadline,compact=True,ignored_copper={uid})
    elif oracle.board is not board:raise ValueError('Via oracle belongs to a different board')
    if deadline is not None:oracle.deadline=min(oracle.deadline or float('inf'),deadline)
    width=via.GetWidth(p.F_Cu)
    if via.GetWidth(p.B_Cu)!=width:raise ValueError('Via displacement requires uniform outer annuli')
    oracle.via_width=width;oracle.drill=via.GetDrill()
    if not oracle.via_clear(destination,net):return None
    contacts={}
    for layer in (p.F_Cu,p.B_Cu):
        shape=via.GetEffectiveShape(layer)
        for cell in oracle.cells(shape.BBox()):
            for other_id,other,othernet,_ in oracle.buckets[layer].get(cell,()):
                if othernet==net and p.SHAPE.Collide(shape,other,0):contacts[layer,other_id]=other
    circle=p.SHAPE_CIRCLE(vec(destination),width//2)
    # An annulus can keep touching the old copper without a new bridge.
    # A face with no explicit contact must stay free for the target route;
    # selected planes are refilled and independently checked by the caller.
    needed={layer for (layer,_),contact in contacts.items() if not p.SHAPE.Collide(circle,contact,0)}
    paths={layer:oracle.leg(old,destination,layer,net) for layer in needed}
    if any(path is None for path in paths.values()):return None
    bridges={layer:[p.SHAPE_SEGMENT(vec(a),vec(z),oracle.track_width) for a,z in zip(path,path[1:])]
             for layer,path in paths.items()}
    if any(not p.SHAPE.Collide(circle,contact,0) and not any(p.SHAPE.Collide(leg,contact,0) for leg in bridges.get(layer,()))
           for (layer,_),contact in contacts.items()):return None
    if oracle.expired():return None
    via.SetPosition(vec(destination))
    for layer,path in paths.items():oracle.add_path(path,layer,net)
    return {'uuid':uid,'net':net,'from_nm':old,'to_nm':destination,'added_ids':oracle.added}
