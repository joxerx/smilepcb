"""Collision-witnessed ownership proposals for padless plane-net branches.

Selection is not permission to delete copper. The caller must refill zones,
prove retained conductor and pad connectivity, and qualify the final candidate.
"""
import time


def candidate_plane_branches(board,witness_ids,protected_ids=()):
    """Return complete witnessed branch IDs and ownership evidence, or None.

    A branch has one retained via/junction anchor and one free endpoint when
    filled zones are excluded. Pads, vias, bridges and whole floating islands
    are never selected for deletion. This function does not mutate the board.
    """
    from smilepcb.native_worker import p
    from .bundle_polish import extract_chains
    witnesses=set(witness_ids);protected=set(protected_ids)
    if not witnesses or any(not isinstance(u,str) for u in witnesses|protected):return None
    items={q.m_Uuid.AsString():q for q in board.GetTracks()}
    zones=[q for q in board.Zones() if not q.GetIsRuleArea() and q.GetNetname()]
    nets={q.GetNetname() for q in zones}-{'USB_DP','USB_DM'}
    if not witnesses<=items.keys() or witnesses&protected:return None
    if any(items[u].GetClass()!='PCB_TRACK' or items[u].IsLocked() or items[u].GetNetname() not in nets for u in witnesses):return None
    deadline=time.monotonic()+10
    try:
        # Plane fills must not manufacture terminal anchors at every endpoint.
        rows=extract_chains(board,eligible_nets=nets,protected_ids=protected,deadline=deadline,
                            deferred_zone_ids=[q.m_Uuid.AsString() for q in zones],
                            max_points=4096,min_length_nm=0)
        selected=[];covered=set();removed=set()
        pads=[q for fp in board.GetFootprints() for q in fp.Pads()]
        for row in rows:
            ids=set(row['ids']);hits=witnesses&ids
            if not hits:continue
            if (not ids or ids&removed or len(ids)!=len(row['ids']) or not ids<=items.keys()
                    or ids&protected or row['net'] not in nets):return None
            face=row['layer'];path=[tuple(q) for q in row['path']]
            if face not in (p.F_Cu,p.B_Cu) or len(path)<2 or path[0]==path[-1]:return None
            for u in ids:
                q=items[u]
                if (q.GetClass()!='PCB_TRACK' or q.IsLocked() or q.GetNetname()!=row['net']
                        or q.GetLayer()!=face or q.GetWidth()!=row['width']):return None
            shapes=[items[u].GetEffectiveShape(face) for u in ids]
            if any(pad.IsOnLayer(face) and any(p.SHAPE.Collide(shape,pad.GetEffectiveShape(face),0) for shape in shapes) for pad in pads):return None
            ends=(path[0],path[-1]);contacts=[set(),set()]
            for uid,item in items.items():
                if time.monotonic()>=deadline:raise TimeoutError('Plane branch ownership deadline')
                if uid in ids or item.GetNetname()!=row['net'] or not item.IsOnLayer(face):continue
                shape=item.GetEffectiveShape(face)
                if not any(p.SHAPE.Collide(candidate,shape,0) for candidate in shapes):continue
                touched=[i for i,q in enumerate(ends) if p.SHAPE.Collide(p.SHAPE_CIRCLE(p.VECTOR2I(*q),0),shape,0)]
                # Interior branches or cap-only contacts need a fuller contract.
                if len(touched)!=1:return None
                contacts[touched[0]].add(uid)
            anchored=[i for i,values in enumerate(contacts) if values]
            if len(anchored)!=1:return None
            anchor=anchored[0];anchor_ids=contacts[anchor]
            via_anchor=any(items[u].GetClass()=='PCB_VIA' for u in anchor_ids)
            track_junction=sum(items[u].GetClass()=='PCB_TRACK' for u in anchor_ids)>=2
            if not via_anchor and not track_junction:return None
            selected.append(dict(net=row['net'],layer=face,width=row['width'],ids=sorted(ids),path=path,
                witness_ids=sorted(hits),zone_ids=sorted(q.m_Uuid.AsString() for q in zones if q.GetNetname()==row['net']),
                anchor=ends[anchor],anchor_ids=sorted(anchor_ids),free_end=ends[1-anchor]))
            removed.update(ids);covered.update(hits)
        if covered!=witnesses:return None
        return dict(removed_ids=sorted(removed),ownership=selected,requires_filled_connectivity_proof=True,
                    scope='Ownership selection only; no electrical deletion proof or mutation')
    except TimeoutError:
        return None
