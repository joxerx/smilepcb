"""Read-only ownership of bounded source-pad copper with proven contact stars."""
from smilepcb.native_worker import p


def owned_pad_stubs(board,pad,chain_ids,protected_ids,oracle):
    """Return extra track IDs whose removal leaves retained branches on the pad.

    Candidates may extend slightly beyond the land. The actual Native shapes,
    rather than a bounding rectangle, prove both pad contact and every retained
    same-net contact. Vias, locked copper, and protected copper remain retained.
    """
    chain=set(chain_ids);protected=set(protected_ids);candidates={}
    minimum=board.GetDesignSettings().m_MinClearance
    for item in board.GetTracks():
        uid=item.m_Uuid.AsString()
        if (uid in chain or uid in protected or item.IsLocked()
                or item.GetClass()!='PCB_TRACK' or item.GetNetname()!=pad.GetNetname()):continue
        face=item.GetLayer()
        if not pad.IsOnLayer(face):continue
        land=pad.GetEffectiveShape(face);shape=item.GetEffectiveShape(face)
        if not p.SHAPE.Collide(shape,land,0):continue
        box=land.BBox();other=shape.BBox();margin=max(minimum,item.GetWidth())+(item.GetWidth()+1)//2
        if (other.GetLeft()<box.GetLeft()-margin or other.GetRight()>box.GetRight()+margin
                or other.GetTop()<box.GetTop()-margin or other.GetBottom()>box.GetBottom()+margin):continue
        candidates[uid]=(face,shape,land)
    ignored=chain|set(candidates)|{pad.m_Uuid.AsString()};owned=set()
    for uid,(face,shape,land) in candidates.items():
        seen=set();safe=True
        for cell in oracle.cells(shape.BBox()):
            for other_id,other,net,_ in oracle.buckets[face].get(cell,()):
                if other_id in seen or other_id in ignored or net!=pad.GetNetname():continue
                seen.add(other_id)
                if p.SHAPE.Collide(shape,other,0) and not p.SHAPE.Collide(other,land,0):safe=False;break
            if not safe:break
        if safe:owned.add(uid)
    return owned
