"""Discover actual plane-track blockers of the first source access carriers.

Witnesses are ownership proposals only. They authorize neither deletion nor a
future-plane connectivity assumption; callers still qualify a fresh final fill.
"""
import math
import time

from .attachment_policy import ranked_departures
from .axis_replay import replay_track_axis
from .polish_geometry import valid_shape


def source_attachment_witnesses(board,group,axis,*,bounds,protected_ids,deadline,deferred_zone_ids=()):
    """Return collision IDs and diagnostics without searching or mutating BOARD.

    The fixed prefix and expanded body share one exact virtual world. A carrier
    contributes all its collision IDs only when every hit is an unlocked,
    unprotected original straight track on an actual ordinary-zone net.
    Unknown objects, ghost copper, pads, vias and mixed hard blockers exclude
    the complete carrier. Other source/axis layer combinations are skipped.
    """
    from smilepcb.native_worker import p,snapshot
    from .local_router import vec
    from .virtual_copper import VirtualCopper
    from .obstacle_bundles import _expanded,_add_checked
    result=dict(witness_ids=[],carriers=[])
    def budget():
        if time.monotonic()>=deadline:raise TimeoutError('Source attachment witness deadline')
    budget()
    before=snapshot(board);protected=set(protected_ids)
    ids={uid for row in group for uid in row['ids']};names=[row['net'] for row in group]
    widths={row['width'] for row in group}
    if (len(group)<2 or len(names)!=len(set(names)) or len(widths)!=1
            or not ids<=before['tracks'].keys() or ids&protected):
        return dict(result,reason='Invalid or protected source group')
    # Validate the supplied axis topology; expanded body clearance follows.
    valid=replay_track_axis(axis,bounds=bounds,layers=(p.F_Cu,p.B_Cu),
                            edge_clear=lambda *args:True,goal_accept=lambda edges:True)
    if not valid['accepted']:return dict(result,reason=valid['reason'])
    items={item.m_Uuid.AsString():item for item in board.GetTracks()}
    zones={}
    for zone in board.Zones():
        if not zone.GetIsRuleArea() and zone.GetNetname():
            zones.setdefault(zone.GetNetname(),[]).append(zone.m_Uuid.AsString())
    width=next(iter(widths))
    world=VirtualCopper(board,compact=True,ignored_copper=ids,track_width_nm=width,
                        deadline=deadline,deferred_zone_ids=deferred_zone_ids)
    try:
        for row in group:
            budget()
            for face,path in row['window']['fixed_legs']:world.add_path(path,face,row['net'])
            for position in row['window']['fixed_vias']:
                matches=[track for uid,track in before['tracks'].items() if uid in row['ids']
                         and track['class']=='PCB_VIA' and tuple(track['start'])==tuple(position)]
                if len(matches)!=1:return dict(result,reason='Missing unique fixed via witness')
                old_width,old_drill=world.via_width,world.drill
                world.via_width,world.drill=matches[0]['width'],matches[0]['drill']
                try:world.add_via(position,row['net'])
                finally:world.via_width,world.drill=old_width,old_drill
        gap=max(world.minimum_clearance,*[world.net_clearances.get(net,0) for net in names])
        pitch=math.ceil((width+gap+25000)/1000)*1000
        heading=tuple(group[0]['heading']);normal=(-heading[1],heading[0])
        order=sorted(range(len(group)),key=lambda i:sum(
            group[i].get('pad_position',group[i]['window']['source'])[k]*normal[k] for k in (0,1)))
        offsets={names[i]:round((rank-(len(group)-1)/2)*pitch) for rank,i in enumerate(order)}
        body=_expanded(axis,names,offsets,width)
        for net in names:
            for operation in body[net]:
                budget()
                if not _add_checked(world,operation):return dict(result,reason='Expanded body blocked')
        witnesses=set()
        for row in group:
            budget();window=row['window'];net=row['net'];face=window['source_layer']
            record=dict(net=net,layer=face,path=None,collision_ids=[],eligible=False)
            result['carriers'].append(record)
            if face!=axis[0]['layer']:
                record['reason']='Source and axis faces differ';continue
            start=next(op['path'][0] for op in body[net] if op['kind']=='track')
            carrier=next(iter(ranked_departures(tuple(window['source']),tuple(start),row['heading'])),None)
            record['path']=carrier
            if (carrier is None or not valid_shape(carrier)
                    or any(not (bounds[0]<=x<=bounds[2] and bounds[1]<=y<=bounds[3]) for x,y in carrier)):
                record['reason']='First source carrier violates geometry policy';continue
            hits=set()
            for a,z in zip(carrier,carrier[1:]):
                budget();hits.update(world.collisions(p.SHAPE_SEGMENT(vec(a),vec(z),width),face,net))
            record['collision_ids']=sorted(hits)
            eligible=bool(hits) and all(uid in items and uid not in protected and uid not in ids
                and items[uid].GetClass()=='PCB_TRACK' and not items[uid].IsLocked()
                and items[uid].GetNetname() in zones for uid in hits)
            record['eligible']=eligible
            record['reason']='Plane tracks only' if eligible else 'No collisions' if not hits else 'Mixed or ineligible blockers'
            if eligible:
                record['plane_zone_ids']=sorted({zone_id for uid in hits for zone_id in zones[items[uid].GetNetname()]})
                witnesses.update(hits)
        result['witness_ids']=sorted(witnesses)
        return result
    finally:
        if snapshot(board)!=before:raise ValueError('Source witness preflight mutated original board')
