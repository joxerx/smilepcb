"""Shared native manufactured copper and net-clearance inputs.

These are conservative local checks. Custom native DRC rules are still
evaluated by the final KiCad admission gate.
"""
from .native_worker import p


def copper_graphics(board):
    """Include copper artwork and footprint fields, independent of net names."""
    items=list(board.GetDrawings())
    for footprint in board.GetFootprints():
        items.extend(footprint.GraphicalItems())
        items.extend(footprint.GetFields())
    seen=set()
    for item in items:
        uid=item.m_Uuid.AsString()
        if uid in seen:continue
        seen.add(uid)
        layer=item.GetLayer()
        if layer not in (p.F_Cu,p.B_Cu):continue
        # A no-net artwork object is never electrically the routed net.
        net=item.GetNetname() if hasattr(item,'GetNetname') else ''
        yield uid,item.GetEffectiveShape(layer),net or None,layer


def own_clearance(item,layer,minimum):
    return max(minimum,item.GetOwnClearance(layer),item.GetLocalClearance() or 0)+1000


def net_clearances(board,minimum):
    result={}
    for item in [*board.GetTracks(),*(pad for fp in board.GetFootprints() for pad in fp.Pads())]:
        for layer in (p.F_Cu,p.B_Cu):
            if item.IsOnLayer(layer):
                net=item.GetNetname()
                result[net]=max(result.get(net,0),own_clearance(item,layer,minimum))
    return result
