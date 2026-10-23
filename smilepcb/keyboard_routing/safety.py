"""Native placement safeguards for additive parts, not relaxed board rules."""
from smilepcb.native_worker import p
import time


def require_via_land_clearance(board,deadline=None):
    """Check saved copper independently, including imported ordinary vias."""
    cell=p.FromMM(4);gap=p.FromMM(.1);lands={}
    def budget():
        if deadline is not None and time.monotonic()>=deadline:
            raise TimeoutError('Via-to-solder-land verification budget exhausted')
    def cells(box,padding=0):
        for x in range((box.GetLeft()-padding)//cell,(box.GetRight()+padding)//cell+1):
            for y in range((box.GetTop()-padding)//cell,(box.GetBottom()+padding)//cell+1):yield x,y
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            budget()
            if pad.GetAttribute()!=p.PAD_ATTRIB_SMD:continue
            for layer in (p.F_Cu,p.B_Cu):
                if not pad.IsOnLayer(layer):continue
                shape=pad.GetEffectiveShape(layer)
                entry=(pad.m_Uuid.AsString(),layer,fp.GetReference(),pad.GetNumber(),shape)
                for key in cells(shape.BBox(),gap):lands.setdefault(key,[]).append(entry)
    count=0
    for via in board.GetTracks():
        budget()
        if not isinstance(via,p.PCB_VIA):continue
        count+=1;hole=via.GetEffectiveHoleShape();seen=set()
        for key in cells(hole.BBox()):
            for uid,layer,ref,pin,shape in lands.get(key,()):
                if (uid,layer) in seen:continue
                seen.add((uid,layer))
                if p.SHAPE.Collide(hole,shape,gap):
                    raise ValueError('Via drill is too close to solder land '+ref+'/'+pin+': '+via.m_Uuid.AsString())
    return {'vias_checked':count,'drill_to_solder_land_min_mm':.1}


def pad_gap(a, b, layer):
    mask = p.F_Mask if layer == p.F_Cu else p.B_Mask
    opening = ((a.GetSolderMaskExpansion(layer) if a.IsOnLayer(mask) else 0) +
               (b.GetSolderMaskExpansion(layer) if b.IsOnLayer(mask) else 0))
    return max(p.FromMM(.2), a.GetLocalClearance() or 0, b.GetLocalClearance() or 0,
               opening) + p.FromMM(.01)


def pads_conflict(a, b, layer):
    if a.GetNetCode() and a.GetNetCode() == b.GetNetCode():
        return False
    return a.GetEffectiveShape(layer).Collide(b.GetEffectiveShape(layer), pad_gap(a,b,layer))


def prepare_added_via(via):
    via.SetFrontTentingMode(p.TENTING_MODE_TENTED)
    via.SetBackTentingMode(p.TENTING_MODE_TENTED)
