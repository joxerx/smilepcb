"""Sealed early controller corridors, using existing geometry and Native proof.

This adds no selector/search allowance: at most 48 complete proposals share the
caller's monotonic cutoff. All source heads remain fixed. A plan is a one-shot
obligation, reproved against the actual field before additive atomic emission.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import math
import time

from .controller_corridor_geometry import group_corridors, corridor_proposals
from .corridor_reservations import reserve_corridors

class CorridorUnavailable(ValueError):
    """The optional complete-template search exhausted its candidate set."""


_ISSUER = object()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _check(deadline):
    if not math.isfinite(deadline) or time.monotonic() >= deadline:
        raise TimeoutError('Controller corridor shared deadline exhausted')


def _source_key(port):
    from .interface_launches import interface_identity
    return interface_identity(port)


@contextmanager
def _launch_scope(ports):
    from .interface_launches import current_launch_inventory,interface_identity,reserve_launches
    parent=current_launch_inventory();merged={} if parent is None else deepcopy(parent.ports)
    resolved=set() if parent is None else set(parent.resolved)
    held=set() if parent is None else set(parent.repair_reserved)
    for port in ports:
        key=interface_identity(port)
        if merged.get(key)!=port:resolved.discard(key)
        merged[key]=deepcopy(port)
    with reserve_launches(list(merged.values()),resolved=resolved,repair_reserved=held):yield


def _departure_context(board,ports,width):
    from .interface_launches import current_launch_inventory
    from .port_policy import PortPolicy
    with _launch_scope(ports):
        complete=list(current_launch_inventory().ports.values())
        # Geometry binding includes every actual peer envelope, independently
        # of closure lifecycle. Active Native policy still keeps its lifecycle.
        policy=PortPolicy(board,complete,track_width_nm=width)
        return [dict(source_key=list(_source_key(q)),waypoint_nm=policy.departure_waypoint_nm(q,width_nm=width))
            for q in sorted(ports,key=_source_key)]


def _envelopes(routes, rules, *, prefix):
    records = []
    for i, row in enumerate(routes):
        for j, (face, path) in enumerate(row['route']['legs']):
            records.append(dict(id=f'{prefix}-{i}-leg-{j}', layer=face, nets=[row['net']],
                path=deepcopy(path), width_nm=rules['track_width_nm']+2*rules['clearance_nm'],source_key=deepcopy(row['source_key'])))
        for j, point in enumerate(row['route']['vias']):
            for face in (0, 2):
                records.append(dict(kind='via', id=f'{prefix}-{i}-via-{j}-{face}', layer=face,
                    nets=[row['net']], center_nm=tuple(point),
                    radius_nm=_via_radius(rules),source_key=deepcopy(row['source_key'])))
    return records


def _via_radius(rules):
    process=rules.get('generation',{})
    annulus=(rules['via_width_nm']+1)//2
    drill=(rules['drill_nm']+1)//2
    return max(annulus+max(rules['clearance_nm'],process.get('via_to_smd_clearance_nm',0),
        process.get('via_to_hole_clearance_nm',0)),
        drill+rules.get('hole_clearance_nm',0),drill+rules.get('drill_land_clearance_nm',0))


def _actual_targets(board,cfg,ports):
    """All numbered non-alias target pads count, including controller auxiliaries."""
    parts={q['ref']:q for q in cfg['parts']};aliases={u for q in ports for u in q['pad_ids']}
    names={q['net'] for q in ports};targets={};pads={}
    for footprint in board.GetFootprints():
        ref=footprint.GetReference()
        for pad in footprint.Pads():
            if not pad.GetNumber():continue
            uid=pad.m_Uuid.AsString();net=pad.GetNetname()
            if parts.get(ref,{}).get('nets',{}).get(pad.GetNumber())!=net:
                raise ValueError('Controller corridor actual pad/model binding differs')
            if net not in names:continue
            layers=[face for face in (0,2) if pad.IsOnLayer(face)]
            pads[uid]=dict(ref=ref,number=pad.GetNumber(),net=net,
                point=[pad.GetPosition().x,pad.GetPosition().y],layers=layers)
            if uid not in aliases:
                if not layers:raise ValueError('Controller corridor target lacks physical copper face')
                targets.setdefault(net,[]).append(dict(pad_id=uid,point=tuple(pads[uid]['point']),layers=layers))
    for rows in targets.values():rows.sort(key=lambda q:q['pad_id'])
    return pads,targets


def _binding(context, cfg, ports):
    # New field copper is permitted. Actual pads, targets, all retained source
    # copper, model, process rules, and terminal metadata are immutable.
    return dict(model=deepcopy(cfg), pads=deepcopy(context['pads']),
        targets=deepcopy(context['targets']), rules=deepcopy(context['rules']),
        bounds=deepcopy(context['bounds']), ports=deepcopy(ports),
        departure_waypoints=deepcopy(context.get('departure_waypoints',[])),
        tracks=deepcopy(context['tracks']))


@dataclass
class ControllerCorridorPlan:
    binding: dict
    receipt: dict
    envelopes: list
    seal: str
    _issuer: object = field(repr=False)
    phase: str = 'planned'

    def _payload(self):
        return dict(binding=self.binding, receipt=self.receipt, envelopes=self.envelopes)

    def verify(self, board, cfg, *, deadline):
        _check(min(deadline,self.receipt['valid_until']))
        if self.phase != 'planned':
            raise ValueError('Controller corridor plan already consumed or rejected')
        if self._issuer is not _ISSUER or self.seal != _digest(self._payload()):
            raise ValueError('Controller corridor plan binding changed')
        backend = _backend()
        context = backend.context(board, cfg, self.binding['ports'])
        actual = _binding(context, cfg, self.binding['ports'])
        retained = actual.pop('tracks')
        expected = dict(self.binding); expected.pop('tracks')
        if (actual != expected or any(retained.get(u) != q for u, q in self.binding['tracks'].items())):
            raise ValueError('Controller corridor plan binding changed')
        _check(deadline)
        return backend, context

    @contextmanager
    def reserve(self, board, cfg, *, deadline):
        self.verify(board, cfg, deadline=deadline)
        with reserve_corridors(self.envelopes):
            yield self

    def commit(self, board, cfg, *, deadline):
        with _launch_scope(self.binding['ports']):
            return self._commit(board,cfg,deadline=min(deadline,self.receipt['valid_until']))

    def _commit(self, board, cfg, *, deadline):
        backend, context = self.verify(board, cfg, deadline=deadline)
        before = backend.snapshot(board)
        world = backend.world(board, context, deadline)
        mark = world.checkpoint()
        routes = [row for group in self.receipt['groups'] for row in group['routes']]
        try:
            closed=backend.closed(board,routes,context,deadline)
            remaining=[row for row in routes if row['net'] not in closed]
            with reserve_corridors(self.envelopes):
                for row in remaining:
                    _check(deadline)
                    if not backend.admit(world, row, row['route'], context, deadline):
                        raise ValueError('Controller corridor complete-group reproof rejected')
                _check(deadline)
                added = backend.emit(board, remaining, context, deadline)
                backend.closure(board, routes, deadline,context=context)
                _check(deadline)
            self.phase = 'consumed'
            return dict(accepted=True, plan_sha256=self.seal, added_ids=added,
                source_batch_sha256=self.receipt['source_batch_sha256'],
                actual_pad_closure=True, consumed_by_verified_local=closed, groups=deepcopy(self.receipt['groups']))
        except BaseException:
            backend.rollback(board, before)
            self.phase = 'rejected'
            raise
        finally:
            world.restore(mark)


def plan_controller_corridors(board,cfg,batch,*,deadline,valid_until=None):
    cutoff=deadline if valid_until is None else valid_until
    _check(cutoff);_check(min(deadline,cutoff))
    with _launch_scope(batch.ports):
        return _plan_controller_corridors(board,cfg,batch,deadline=min(deadline,cutoff),valid_until=cutoff)


def _plan_controller_corridors(board, cfg, batch, *, deadline,valid_until):
    """Prove every eligible source-to-real-pad lane before foreign construction."""
    _check(deadline)
    batch.verify(board, cfg, deadline=deadline)
    backend = _backend(); ports = deepcopy(list(batch.ports))
    context = backend.context(board, cfg, ports)
    waypoints={tuple(row['source_key']):row['waypoint_nm'] for row in context.get('departure_waypoints',[])}
    for port in ports:
        key=_source_key(port);port['source_key']=list(key)
        waypoint=waypoints.get(key)
        if waypoint is not None:port['departure_waypoint_nm']=waypoint
    eligible = {q['net'] for q in ports if context['targets'].get(q['net'])}
    missing=[q for q in ports if q['net'] not in eligible]
    local_closures=backend.closed(board,missing,context,deadline) if missing else {}
    if {q['net'] for q in missing}-set(local_closures):
        raise ValueError('Missing actual physical target and whole-net closure proof')
    groups = group_corridors(ports, context['targets'], eligible_nets=eligible)
    world = backend.world(board, context, deadline); initial = world.checkpoint()
    accepted = []; attempts = 0; rules = context['rules']
    try:
        for group in groups:
            checkpoint = world.checkpoint(); chosen = None
            for proposal in corridor_proposals(group, track_width_nm=rules['track_width_nm'],
                    clearance_nm=rules['clearance_nm'], board_bounds_nm=context['bounds'],
                    obstacle_bounds=context['obstacles'], deadline=deadline, max_candidates=48,via_width_nm=rules['via_width_nm'],
                    via_envelope_radius_nm=_via_radius(rules)):
                _check(deadline)
                if attempts >= 48:
                    break
                attempts += 1; world.restore(checkpoint); routes = []
                candidate_context=backend.proposal_context(proposal,context,deadline)
                for source, lane in zip(group, proposal['lanes']):
                    _check(deadline)
                    row = dict(source, **{'path': lane['path']})
                    route = backend.bridge(world, row, candidate_context, deadline)
                    if route is None or not backend.admit(world, row, route, candidate_context, deadline):
                        break
                    routes.append(dict(row, route=route))
                if len(routes) == len(group):
                    chosen = dict(routes=routes, diagnostics=proposal['diagnostics']); break
            if chosen is None:
                raise CorridorUnavailable('No complete controller corridor cohort within shared candidate budget: '+str(getattr(backend,'last_failure',{})))
            accepted.append(chosen)
        _check(deadline)
        routes = [q for group in accepted for q in group['routes']]
        binding = _binding(context, cfg, ports)
        receipt = dict(accepted=True, source_batch_sha256=batch.seal, groups=accepted,
            candidate_attempts=attempts, max_candidates=48, local_target_closures=local_closures,valid_until=valid_until,
            via_width_nm=rules['via_width_nm'], drill_nm=rules['drill_nm'])
        records = _envelopes(routes, rules, prefix='controller-'+_digest(receipt)[:16])
        payload = dict(binding=binding, receipt=receipt, envelopes=records)
        return ControllerCorridorPlan(binding, receipt, records, _digest(payload), _ISSUER)
    finally:
        world.restore(initial)


class _NativeBackend:
    """Small adapter around the established Native physical admission path."""
    def context(self, board, cfg, ports):
        from smilepcb.native_worker import p, snapshot
        from .conditional_banks import _source_physics
        from .generation import current_generation_profile
        from .local_router import LocalRouter
        state = snapshot(board)
        pads,targets=_actual_targets(board,cfg,ports)
        names={q['net'] for q in ports}
        source_ids={uid for port in ports for uid in port['added_ids']}
        source_physics=_source_physics(board,set(pads)|source_ids)
        settings = board.GetDesignSettings()
        width = settings.m_TrackMinWidth or 150000
        # Read process-bound dimensions from the existing oracle, never invent
        # a smaller annulus/clearance in the reservation description.
        oracle = LocalRouter(board,compact=True,track_width_nm=width)
        # Signal pitch uses participating owners; foreign pad/hole guards
        # remain in the unchanged Native oracle and separate physical rules.
        gap = max(oracle.minimum_clearance,173000,*(oracle.net_clearances.get(net,0) for net in names))
        waypoints=_departure_context(board,ports,width)
        rules = dict(track_width_nm=width, clearance_nm=gap, via_width_nm=oracle.via_width,
            drill_nm=oracle.drill, profile=current_generation_profile().name,
            generation=vars(oracle.generation), source_physics=source_physics,
            edge_clearance_nm=oracle.edge_clearance, hole_clearance_nm=oracle.native_hole_clearance,
            drill_land_clearance_nm=p.FromMM(.1))
        box = board.GetBoardEdgesBoundingBox()
        bounds = [box.GetLeft(),box.GetTop(),box.GetRight(),box.GetBottom()]
        obstacles = []
        for footprint in board.GetFootprints():
            for pad in footprint.Pads():
                if pad.GetNetname() in names:
                    continue
                for face in (p.F_Cu,p.B_Cu):
                    if pad.IsOnLayer(face):
                        b=pad.GetEffectiveShape(face).BBox()
                        if b.GetWidth()>0 and b.GetHeight()>0:
                            obstacles.append([b.GetLeft(),b.GetTop(),b.GetRight(),b.GetBottom()])
        return dict(pads=pads, tracks=state['tracks'], targets=targets,
            rules=rules, bounds=bounds, obstacles=obstacles,departure_waypoints=waypoints)

    def world(self, board, context, deadline):
        from .virtual_copper import VirtualCopper
        return VirtualCopper(board,deadline=deadline,compact=True,
            track_width_nm=context['rules']['track_width_nm'])

    def proposal_context(self,proposal,context,deadline):
        """Build each fixed XY carrier once before the first cohort member."""
        if len(proposal['lanes'])<2:return context
        from smilepcb.native_worker import p
        from .local_router import vec
        carriers=[];width=context['rules']['track_width_nm']
        for lane in proposal['lanes']:
            _check(deadline)
            carriers.append(dict(source_key=deepcopy(lane['source_key']),net=lane['net'],
                shapes=tuple(p.SHAPE_SEGMENT(vec(a),vec(z),width) for a,z in zip(lane['path'],lane['path'][1:]))))
        return dict(context,cohort_carriers=carriers,cohort_via_radius_nm=_via_radius(context['rules']))

    def bridge(self, world, row, context, deadline):
        from itertools import islice
        from smilepcb.native_worker import p
        from .local_router import vec
        from .column_bridges import bridge_carrier
        from .obstacle_bundles import _physical_connection
        width=world.track_width;net=row['net'];face=row['source_layer']
        evidence=[];queries=dict(track=0,via=0,pair=0)
        def clear(a,z,f):
            queries['track']+=1;shape=p.SHAPE_SEGMENT(vec(a),vec(z),width)
            accepted=world.clear(shape,f,net)
            if not accepted and len(evidence)<4 and sum(q.get('kind')=='track' for q in evidence)<2:
                evidence.append(dict(kind='track',layer=f,segment=[a,z],collisions=list(islice(world.collisions(shape,f,net),4))))
            return accepted
        def via_clear(point):
            queries['via']+=1;accepted=world.via_clear(point,net);peer=None
            if accepted and context.get('cohort_carriers'):
                shape=p.SHAPE_CIRCLE(vec(point),context['cohort_via_radius_nm'])
                # A through-via occupies both faces, regardless of the peer's
                # eventual layer choice. Tracks can still use layer bridges.
                for carrier in context['cohort_carriers']:
                    owner=carrier['net']
                    if owner!=net and any(p.SHAPE.Collide(shape,other,0) for other in carrier['shapes']):
                        accepted=False;peer=dict(net=owner,source_key=carrier['source_key']);break
            if not accepted and len(evidence)<4 and sum(q.get('kind')=='via' for q in evidence)<2:
                shape=p.SHAPE_CIRCLE(vec(point),world.via_width//2)
                evidence.append(dict(kind='via',position=point,via_width_nm=world.via_width,drill_nm=world.drill,
                    future_carrier_net=peer,collisions={str(f):list(islice(world.collisions(shape,f,net,via=True),4)) for f in (0,2)}))
            return accepted
        def pair(a,z):
            queries['pair']+=1
            return math.dist(a,z)>=world.via_width+world.minimum_clearance and world.via_pair_clear(a,z)
        _check(deadline)
        route=bridge_carrier(row['path'],face,p.B_Cu if face==p.F_Cu else p.F_Cu,
            start_layers={face},end_layers=set(row['target_layers']),secondary_length_factor=2,
            via_cost_nm=400000,deadline=deadline,clear=clear,via_clear=via_clear,via_pair_clear=pair)
        if route is not None:
            diagnostics={}
            if _physical_connection(world,route,row['source'],row['target'],net,context['bounds'],emit=False,diagnostics=diagnostics):
                return route
            if len(evidence)<4:evidence.append(diagnostics)
        self.last_failure=dict(reason='no-native-layer-bridge',net=net,source=row['source'],
            source_heading=row['heading'],target=row['target'],target_layers=row['target_layers'],
            queries=queries,rejections=evidence)
        return None

    def admit(self, world, row, route, context, deadline):
        from .obstacle_bundles import _physical_connection
        _check(deadline)
        diagnostics={}
        accepted=_physical_connection(world,route,row['source'],row['target'],row['net'],context['bounds'],diagnostics=diagnostics)
        self.last_failure=diagnostics
        return accepted

    def emit(self, board, routes, context, deadline):
        from .local_router import LocalRouter
        router=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=context['rules']['track_width_nm'])
        for row in routes:
            _check(deadline)
            for face,path in row['route']['legs']:
                router.add_path(path,face,row['net'])
            for point in row['route']['vias']:
                router.add_via(point,row['net'])
        board.BuildConnectivity()
        return list(router.added)

    def closed(self, board, routes, context, deadline):
        from .native_graph import copper_graph
        _check(deadline)
        graph=copper_graph(board,{q['net'] for q in routes},deadline,include_zones=False)
        ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])}
        result={}
        for row in routes:
            required={uid for uid,pad in context['pads'].items() if pad['net']==row['net']}
            if required and required<=ids.keys() and len({graph['find'](ids[u]) for u in required})==1:
                result[row['net']]=sorted(required)
        return result

    def closure(self, board, routes, deadline,*,context):
        from .native_graph import copper_graph
        names={pad['net'] for pad in context['pads'].values()}
        graph=copper_graph(board,names,deadline,include_zones=False)
        ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])}
        for net in names:
            required={uid for uid,pad in context['pads'].items() if pad['net']==net}
            if not required<=ids.keys() or len({graph['find'](ids[u]) for u in required})!=1:
                raise ValueError('Controller corridor lacks actual source/target Native pad closure')
        return True

    def snapshot(self, board):
        from smilepcb.native_worker import snapshot
        return snapshot(board)

    def rollback(self, board, before):
        from smilepcb.native_worker import snapshot
        for item in list(board.GetTracks()):
            if item.m_Uuid.AsString() not in before['tracks']:
                board.Delete(item)
        board.BuildConnectivity()
        if snapshot(board)!=before:
            raise ValueError('Controller corridor rollback changed retained source')


def _backend():
    return _NativeBackend()


def collector_plan_assignment(board,cfg,handoff,source,*,deadline):
    """An authoritative early assignment permits no second permutation."""
    handoff.corridor_plan.verify(board,cfg,deadline=deadline)
    expected=[deepcopy(q) for q in handoff.batch.ports if q['net'] in handoff.batch.matrix_nets]
    if source['ports']!=expected:
        raise ValueError('Early corridor collector source differs from sealed assigned ports')
    return {q['net']:q['net'] for q in expected},deepcopy(handoff.corridor_assignment['collector_plans'])
