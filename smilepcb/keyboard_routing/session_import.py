"""Replace editable native copper while retaining exact fixed objects."""
from smilepcb.native_worker import p,snapshot
from smilepcb.routing_policy import fixed_copper_ids
from smilepcb.routing_session import session_via_contacts,require_via_inventory
from smilepcb.routing_copper import routing_delta


def apply_open_session(board,session,protected_ids):
    """Mutate only this in-memory candidate; the caller owns validation/save."""
    before=snapshot(board);protected=fixed_copper_ids(board,protected_ids)
    fixed={uid:before['tracks'][uid] for uid in protected}
    expected=session_via_contacts(session.read_text())
    imported=p.LoadBoard(board.GetFileName())
    for item in list(imported.GetTracks()):imported.Delete(item)
    if not p.ImportSpecctraSES(imported,str(session)):raise ValueError('SES import failed')
    require_via_inventory(expected,[(t.GetNetname(),t.GetPosition().x,t.GetPosition().y)
                                   for t in imported.GetTracks() if isinstance(t,p.PCB_VIA)])
    returned=snapshot(imported)['tracks']
    if any(v['class'] not in ('PCB_TRACK','PCB_VIA') for v in returned.values()):
        raise ValueError('Unsupported newly returned copper')
    linear={uid:v for uid,v in fixed.items() if v['class']=='PCB_VIA' or
            v['class']=='PCB_TRACK' and v['start']!=v['end']}
    if any(v['class'] not in ('PCB_ARC','PCB_TRACK') for uid,v in fixed.items() if uid not in linear):
        raise ValueError('Unsupported fixed source object')
    # Fixed arcs/dots are native objects, not approximations. A router may omit
    # system-fixed copper from its session; supplement only the coverage oracle.
    supplement=dict(returned);supplement.update({'fixed-'+uid:v for uid,v in linear.items()})
    delta=routing_delta(linear,supplement);by_id={t.m_Uuid.AsString():t for t in imported.GetTracks()}
    for uid,shape in delta:
        if board.FindNet(shape['net']) is None:raise ValueError('Router returned an unknown net')
    for item in list(board.GetTracks()):
        if item.m_Uuid.AsString() not in protected:board.Delete(item)
    for uid,shape in delta:
        clone=by_id[uid].Duplicate();clone.SetNetCode(board.FindNet(shape['net']).GetNetCode());clone.SetLocked(False)
        if shape['class']=='PCB_TRACK':
            clone.SetStart(p.VECTOR2I(*shape['start']));clone.SetEnd(p.VECTOR2I(*shape['end']))
        board.Add(clone)
    board.BuildConnectivity();after=snapshot(board)
    if after['footprints']!=before['footprints'] or any(after['tracks'].get(uid)!=v for uid,v in fixed.items()):
        raise ValueError('Global routing changed fixed copper or components')
    return {'protected_tracks':len(fixed),'protected_ids':sorted(protected),
            'replaced_editable_objects':len(before['tracks'])-len(fixed),
            'fixed_arcs_or_dots':len(fixed)-len(linear),'returned_copper':len(returned),
            'added_objects':len(delta),'final_copper':len(after['tracks']),
            'session_via_count':sum(expected.values())}
