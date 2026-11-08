"""Atomic multi-chain routing, including closure over movable blockers."""
import math
import time

from smilepcb.native_worker import p, snapshot
from .local_router import LocalRouter, vec
from .column_bridges import bridge_carrier
from .layer_policy import terminal_layers
from .visual_geometry import safe_route
from .visual_polish import _copy, _parts, _refill
from .via_junction_polish import _pad_contacts_clear, _via_own_clear
from .bundle_polish import _joins_clear, retained_contacts_clear
from .free_corridor_geometry import blocker_closure, restoration_paths
from .polish_geometry import octile, shortcut_candidates, valid_shape


def attempt_corridor(board,group,paths,*,preferred_layer,protected_ids,deadline,
                     deferred_zone_ids,blocker_pool,accept,preserve_v45_nets=(),preferred_layers=None):
    before=snapshot(board);base={u for q in group for u in q['ids']};protected=set(protected_ids)
    if len(base)!=sum(len(q['ids']) for q in group) or not base<=before['tracks'].keys():return dict(accepted=False,reason='Stale or overlapping group',blocked_ids=[])
    extras=[];blocked=set();diagnostic=[];plan_failures={};bridge_examples=[]
    def restore():
        for t in list(board.GetTracks()):
            if t.m_Uuid.AsString() not in before['tracks']:board.Delete(t)
        live={t.m_Uuid.AsString() for t in board.GetTracks()}
        for uid,t in copies.items():
            if uid not in live:board.Add(_copy(t))
        _refill(board)
        if snapshot(board)!=before:raise ValueError('Free corridor rollback changed source')
    copies={}
    for retry in range(2):
        ids=base|{u for q in extras for u in q['ids']}
        items={t.m_Uuid.AsString():t for t in board.GetTracks()}
        if ids&protected or not ids<=items.keys():return dict(accepted=False,reason='Protected/stale blocker closure',blocked_ids=sorted(blocked))
        copies={u:_copy(items[u]) for u in ids}
        routes=[];restored=[];reason=None
        try:
            for u in ids:board.Delete(items[u])
            oracle=LocalRouter(board,compact=True,track_width_nm=min(q['width'] for q in [*group,*extras]),deadline=deadline,deferred_zone_ids=deferred_zone_ids)
            def plan(row,path,face):
                oracle.track_width=row['width'];net=row['net'];other=p.B_Cu if face==p.F_Cu else p.F_Cu
                recent=[]
                def clear(a,z,layer):
                    hits=list(oracle.collisions(p.SHAPE_SEGMENT(vec(a),vec(z),row['width']),layer,net))
                    recent.append(dict(a=a,z=z,layer=layer,hits=hits));del recent[:-8]
                    blocked.update(hits);return not hits
                route=bridge_carrier(path,face,other,
                    start_layers=terminal_layers(board,path[0],net,row['start_layer']),
                    end_layers=terminal_layers(board,path[-1],net,row['end_layer']),
                    secondary_length_factor=3,via_cost_nm=400000,clear=clear,
                    via_clear=lambda v:oracle.via_clear(v,net) and _via_own_clear(oracle,v,net,oracle.via_width),
                    via_pair_clear=lambda a,z:math.dist(a,z)>=oracle.via_width+oracle.minimum_clearance and oracle.via_pair_clear(a,z),deadline=deadline)
                def fail(stage):
                    key=net+':'+stage;plan_failures[key]=plan_failures.get(key,0)+1
                    return None
                if route is None:
                    bridge_examples.append(dict(net=net,path=path,recent=recent));del bridge_examples[:-2]
                    return fail('expired' if oracle.expired() else 'no-bridge')
                if not safe_route(route,row['width'],oracle.via_width):return fail('self-contact')
                for layer,leg in route['legs']:
                    if not _joins_clear(board,leg,layer,net,()):return fail('join-angle')
                    if not _pad_contacts_clear(board,leg,layer,net,row['width'],oracle):return fail('pad-contact')
                    if not retained_contacts_clear(board,leg,layer,net,row['width'],oracle=oracle):return fail('retained-contact')
                for layer,leg in route['legs']:oracle.add_path(leg,layer,net)
                for v in route['vias']:oracle.add_via(v,net)
                return route
            for row,path in zip(group,paths):
                route=plan(row,path,preferred_layer)
                if route is None:raise ValueError('Search expiry' if oracle.expired() else 'Primary carrier blocked')
                routes.append(route)
            for row in extras:
                route=None
                for path in restoration_paths(row,deadline=deadline,preserve_v45_nets=preserve_v45_nets):
                    if oracle.expired():break
                    if valid_shape(path):route=plan(row,path,(preferred_layers or {}).get(row['net'],row['start_layer']))
                    if route is not None:break
                if route is None:raise ValueError('Blocker restoration failed')
                restored.append(dict(row=row,route=route,path=path))
            _refill(board);after=snapshot(board)
            if after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=t for u,t in before['tracks'].items() if u not in ids):raise ValueError('Retained copper or components changed')
            if not accept(before,after,routes,restored):raise ValueError('Complete workset gain/quality rejected')
            return dict(accepted=True,routes=routes,restored=restored,removed_ids=sorted(ids),new_ids=sorted(set(after['tracks'])-set(before['tracks'])),blocked_ids=sorted(blocked),expanded_chains=len(extras),diagnostic=diagnostic,plan_failures=plan_failures,bridge_examples=bridge_examples)
        except (ValueError,TimeoutError) as error:reason=str(error)
        except BaseException:
            restore();raise
        restore();diagnostic.append(reason)
        if retry or time.monotonic()+1>=deadline:break
        closed=blocker_closure(blocked,blocker_pool,protected_ids=protected|base)
        extras=[q for q in blocker_pool if set(q['ids'])<=closed and not set(q['ids'])&base]
        # Bound a physical workset, not individual net identities. Its global
        # numbered-pad proof and final DRC still decide admission.
        if not extras or len(extras)>8:break
    return dict(accepted=False,reason=reason,blocked_ids=sorted(blocked),expanded_chains=len(extras),diagnostic=diagnostic,plan_failures=plan_failures,bridge_examples=bridge_examples)
