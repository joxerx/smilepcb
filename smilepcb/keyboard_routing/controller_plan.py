"""Authentic shared source batches and single-use matrix subset handoff."""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass,field
import hashlib,json,time
from types import SimpleNamespace

_ISSUER=object()


def _digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def _check(deadline):
    if time.monotonic()>=deadline:raise TimeoutError('Unified controller proof deadline exhausted')


def _proof_tools():
    from smilepcb.native_worker import snapshot
    from .native_graph import copper_graph
    from .conditional_banks import _representable_heads,_source_physics
    from .generation import current_generation_profile
    return SimpleNamespace(snapshot=snapshot,graph=copper_graph,representable=_representable_heads,
        physics=_source_physics,profile=lambda:current_generation_profile().name)


def _pads(state):return {d['uuid']:(f['ref'],d) for f in state['footprints'].values() for d in f['pads'] if d['number']}


def _bindings(state,cfg):
    parts={q['ref']:q for q in cfg['parts']}
    for ref,d in _pads(state).values():
        if parts.get(ref,{}).get('nets',{}).get(d['number'])!=d['net']:
            raise ValueError('Shared source pad differs from its actual model binding')


@dataclass(frozen=True)
class GeneratedSourceBatch:
    before: dict
    model: dict
    tracks: dict
    pads: dict
    ports: tuple
    matrix_nets: frozenset
    physical_sha256: str
    profile: str
    seal: str
    _issuer: object=field(repr=False)
    lineage: dict=field(default_factory=dict)
    requested_nets: frozenset=field(default_factory=frozenset)
    already_accessible: tuple=field(default_factory=tuple)
    _lease: list=field(default_factory=list,repr=False,compare=False)

    def _payload(self):
        result=dict(before=self.before,model=self.model,tracks=self.tracks,pads=self.pads,ports=self.ports,matrix_nets=sorted(self.matrix_nets))
        if self.lineage:result['lineage']=self.lineage
        result['requested_nets']=sorted(self.requested_nets)
        if self.already_accessible:result['already_accessible']=self.already_accessible
        return result

    def verify(self,board,cfg,*,deadline):
        _check(deadline);t=_proof_tools();state=t.snapshot(board)
        if self._issuer is not _ISSUER or _digest(self._payload())!=self.seal or self.profile!=t.profile() or _digest(cfg)!=_digest(self.model):
            raise ValueError('Shared source batch certificate changed')
        if (any(state['tracks'].get(u)!=q for u,q in {**self.before['tracks'],**self.tracks}.items()) or
                state['footprints']!=self.before['footprints']):
            raise ValueError('Shared source batch or source pads changed')
        _bindings(state,cfg)
        t.representable(board,set(self.tracks))
        if t.physics(board,set(self.tracks)|set(self.pads))!=self.physical_sha256:
            raise ValueError('Shared source native physics changed')
        _check(deadline);return state

    @property
    def fixed_ports(self):return deepcopy([q for q in self.ports if q['net'] not in self.matrix_nets])

    def handoff(self):return PreplannedSources(self)


class PreplannedSources:
    """One lease shared by all handles of an immutable ownership proof."""
    def __init__(self,batch):self.batch=batch;self.frame=None;self.frame_seal=None;self.controller_ownership=None

    @property
    def used(self):return bool(self.batch._lease)

    def take(self,board,cfg,nets,*,deadline):
        if self.used:raise ValueError('Shared source batch already consumed')
        if set(nets)!=set(self.batch.matrix_nets):raise ValueError('Shared source handoff changed matrix scope')
        state=self.batch.verify(board,cfg,deadline=deadline)
        self.frame=state;self.frame_seal=_digest(state);self.batch._lease.append(True)
        ports=deepcopy([q for q in self.batch.ports if q['net'] in nets])
        return dict(accepted=True,ports=ports,added_ids=sorted(u for q in ports for u in q['added_ids']),
            supplier='unified_controller_source_batch',shared_batch_sha256=self.batch.seal)

    def matrix_delta(self,board,cfg,ports,*,deadline):
        """Validate only the existing safe permutation against a real take frame."""
        _check(deadline);batch=self.batch;t=_proof_tools()
        if (not self.used or self.frame is None or self.frame_seal!=_digest(self.frame) or
                batch._issuer is not _ISSUER or batch.seal!=_digest(batch._payload()) or batch.profile!=t.profile()):
            raise ValueError('Unconsumed or changed shared source handoff')
        originals={tuple(q['pad_ids']):q for q in batch.ports if q['net'] in batch.matrix_nets}
        if len(ports)!=len(originals):raise ValueError('Shared matrix source subset changed')
        mapping={};owned=set();seen=set()
        for q in ports:
            key=tuple(q['pad_ids']);old=originals.get(key)
            if old is None or key in seen or q['net'] not in batch.matrix_nets:raise ValueError('Foreign/duplicate matrix source')
            seen.add(key);expected=deepcopy(old);expected['net']=q['net']
            actual={k:v for k,v in q.items() if k!='collector_heading'}
            if actual!=expected:raise ValueError('Matrix permutation changed source geometry metadata')
            if old['net'] in mapping and mapping[old['net']]!=q['net']:raise ValueError('Split matrix signal')
            mapping[old['net']]=q['net'];owned.update(q['added_ids'])
        if set(mapping)!=set(batch.matrix_nets) or set(mapping.values())!=set(batch.matrix_nets):
            raise ValueError('Matrix labels are not a complete permutation')
        expected=deepcopy(self.frame)
        for uid in owned:expected['tracks'][uid]['net']=mapping[expected['tracks'][uid]['net']]
        for f in expected['footprints'].values():
            if f['ref']=='U_MCU':
                for pad in f['pads']:pad['net']=mapping.get(pad['net'],pad['net'])
        if t.snapshot(board)!=expected:raise ValueError('Matrix subset changed fixed/context copper or pads')
        expected_parts=deepcopy(batch.model['parts'])
        for part in expected_parts:
            if part['ref']=='U_MCU':part['nets']={n:mapping.get(net,net) for n,net in part['nets'].items()}
        if cfg['parts']!=expected_parts:raise ValueError('Matrix permutation changed fixed model identities')
        _bindings(expected,cfg);t.representable(board,owned)
        if t.physics(board,set(batch.tracks)|set(batch.pads))!=batch.physical_sha256:
            raise ValueError('Shared source physics changed after permutation')
        _check(deadline);return owned


def certify_source_batch(board,cfg,before,source,*,matrix_nets,requested_nets,deadline,lineage=None):
    """Capture the real complete source delta before any subset or relabeling."""
    _check(deadline);t=_proof_tools();state=t.snapshot(board);_bindings(state,cfg)
    if source.get('accepted') is not True:raise ValueError('Shared source batch requires accepted source')
    ports=deepcopy(source['ports']);delta=set(state['tracks'])-set(before['tracks'])
    owned=[u for q in ports for u in q['added_ids']]
    if (len(owned)!=len(set(owned)) or set(owned)!=delta or set(source['added_ids'])!=delta or
            len(source['added_ids'])!=len(delta) or state['footprints']!=before['footprints'] or
            any(state['tracks'].get(u)!=q for u,q in before['tracks'].items())):
        raise ValueError('Shared source ownership differs from exact fresh generated delta')
    pads=_pads(state);parts={q['ref']:q for q in cfg['parts']};names=set(requested_nets)
    if any(q['net'] not in names for q in ports):raise ValueError('Foreign source function')
    graph=t.graph(board,names,deadline);ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])}
    claimed=[];matrix=set(matrix_nets)
    for q in ports:
        pp=q['pad_ids'];tt=q['added_ids'];claimed+=pp
        if (not pp or not tt or q.get('terminal_id',q.get('via_id')) not in tt or
                any(u not in pads or pads[u][1]['net']!=q['net'] or parts[pads[u][0]]['region']!='controller' for u in pp) or
                not any(pads[u][0]=='U_MCU' for u in pp) or
                any(state['tracks'][u]['net']!=q['net'] or state['tracks'][u]['locked'] or state['tracks'][u]['class'] not in ('PCB_TRACK','PCB_VIA') for u in tt)):
            raise ValueError('Shared source has invalid actual pads or owned copper')
        allowed=set(pp)|set(tt)
        if not allowed<=ids.keys() or len({graph['find'](ids[u]) for u in allowed})!=1:
            raise ValueError('Shared source lacks actual native contact')
        root=graph['find'](ids[pp[0]])
        members={n['item'].m_Uuid.AsString() for i,n in enumerate(graph['nodes']) if graph['find'](i)==root}
        if q['net'] in matrix and members!=allowed:raise ValueError('Matrix source is not an exact isolated owned component')
    if len(claimed)!=len(set(claimed)):raise ValueError('Shared source pads duplicated')
    required={u for u,(ref,d) in pads.items() if ref=='U_MCU' and d['net'] in names}
    covered=set(claimed)
    for q in source.get('already_accessible',[]):
        pp=set(q['pad_ids'])
        if q['net'] in matrix or not pp or not pp<=ids.keys():raise ValueError('Unowned accessible matrix source')
        roots={graph['find'](ids[u]) for u in pp}
        if len(roots)!=1 or not any(u in ids and graph['find'](ids[u]) in roots and parts.get(ref,{}).get('region')!='controller' for u,(ref,d) in pads.items() if d['net']==q['net']):
            raise ValueError('Accessible source has no actual external pad contact')
        covered|=pp
    if not required<=covered or {q['net'] for q in ports if q['net'] in matrix}!=matrix:
        raise ValueError('Shared source omitted outstanding MCU functions')
    accessible=deepcopy(source.get('already_accessible',[]))
    source_pad_ids=set(claimed)|{u for q in accessible for u in q['pad_ids']}
    t.representable(board,delta);source_pads={u:deepcopy(pads[u][1]) for u in source_pad_ids}
    data=dict(before=deepcopy(before),model=deepcopy(cfg),tracks={u:deepcopy(state['tracks'][u]) for u in delta},pads=source_pads,ports=tuple(ports),matrix_nets=sorted(matrix),requested_nets=sorted(names))
    if accessible:data['already_accessible']=accessible
    if lineage:data['lineage']=deepcopy(lineage)
    _check(deadline)
    return GeneratedSourceBatch(data['before'],data['model'],data['tracks'],source_pads,tuple(ports),frozenset(matrix),
        t.physics(board,delta|set(source_pads)),t.profile(),_digest(data),_ISSUER,deepcopy(lineage or {}),frozenset(names),tuple(accessible))



def rebind_source_batch(board,cfg,batch,*,net_mapping,deadline):
    """Validate the old lease's exact permitted delta before resealing it.

    The before frame is deterministically relabeled, never recaptured from a
    board that may already contain changed obligations or long feeders.
    """
    batch.verify(board,cfg,deadline=deadline)
    mapping=dict(net_mapping)
    if set(mapping)!=set(batch.matrix_nets) or set(mapping.values())!=set(batch.matrix_nets):
        raise ValueError('Early corridor assignment requires a whole matrix permutation')
    ports=[deepcopy(q) for q in batch.ports if q['net'] in batch.matrix_nets]
    if all(old==new for old,new in mapping.items()):
        return cfg,batch,dict(accepted=True,mapping=mapping,ports=ports,identity=True)
    from .gpio import apply_exit_assignment
    handoff=batch.handoff();source=handoff.take(board,cfg,batch.matrix_nets,deadline=deadline)
    updated,assignment=apply_exit_assignment(board,cfg,source['ports'],net_mapping=mapping,deadline=deadline)
    handoff.matrix_delta(board,updated,assignment['ports'],deadline=deadline)
    before=deepcopy(batch.before)
    for footprint in before['footprints'].values():
        if footprint['ref']=='U_MCU':
            for pad in footprint['pads']:pad['net']=mapping.get(pad['net'],pad['net'])
    complete=deepcopy(list(batch.ports));replacements={tuple(q['pad_ids']):q for q in assignment['ports']}
    complete=[replacements.get(tuple(q['pad_ids']),q) for q in complete]
    source=dict(accepted=True,ports=complete,added_ids=sorted(batch.tracks),already_accessible=deepcopy(list(batch.already_accessible)))
    recertified=certify_source_batch(board,updated,before,source,matrix_nets=batch.matrix_nets,
        requested_nets=batch.requested_nets,deadline=deadline,
        lineage=dict(parent_seal=batch.seal,mapping=mapping))
    return updated,recertified,assignment


def _local_tools():
    from .controller_local import _members,capture_local_closures
    from .native_graph import copper_graph,route_components
    from .local_router import LocalRouter
    from .interface_launches import reserve_launches
    return SimpleNamespace(members=_members,capture=capture_local_closures,graph=copper_graph,
        route=route_components,router=LocalRouter,reserve=reserve_launches)


def _local_subsets(board,cfg,names):
    _,parts,members=_local_tools().members(board,cfg);result={}
    for net in sorted(names):
        rows=members.get(net,{})
        local={u:deepcopy(q) for u,q in rows.items() if parts[q['ref']]['region']=='controller'}
        if not local:raise ValueError('Controller function has no actual local pad obligations')
        external=sorted(set(rows)-set(local))
        result[net]=dict(pads=local,external_pad_ids=external,controller_only=not external)
    return result


def verify_controller_local_subsets(board,cfg,proof,*,deadline):
    """Actual local aliases must close; an external pad is never waived."""
    _check(deadline)
    _,_,members=_local_tools().members(board,cfg)
    required={net for net,rows in members.items() if any(q['ref']=='U_MCU' for q in rows.values())}
    if set(proof['pad_subsets'])!=required:raise ValueError('Controller-local proof omitted outstanding MCU functions')
    actual=_local_subsets(board,cfg,required)
    if actual!=proof['pad_subsets']:raise ValueError('Controller-local subset pads/bindings/regions changed')
    graph=_local_tools().graph(board,set(actual),deadline,include_zones=False)
    ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])}
    for net,row in actual.items():
        required=set(row['pads'])
        if not required<=ids.keys() or len({graph['find'](ids[u]) for u in required})!=1:
            raise ValueError('Controller-local pad obligation is not closed: '+net)
    _check(deadline);return actual


def close_controller_obligations(board,cfg,names,*,matrix_ports,deadline):
    """Existing component router closes real local subsets with matrix exits live."""
    t=_local_tools();native=_proof_tools();before=native.snapshot(board)
    stop=min(deadline-12,time.monotonic()+20);_check(stop)
    subsets=_local_subsets(board,cfg,names);required={u for row in subsets.values() for u in row['pads']}
    with t.reserve(matrix_ports):
        router=t.router(board,deadline=stop-3,compact=True,track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000)
        count=t.route(board,router,set(subsets),required_pad_ids=required,refresh_components=True,prefer_small_goal=True) if required else 0
        proof=dict(accepted=False,pad_subsets=subsets,connections=count)
        verify_controller_local_subsets(board,cfg,proof,deadline=stop)
        closed=t.capture(board,cfg,deadline=stop)
    if any(row['controller_only'] and net not in closed['nets'] for net,row in subsets.items()):
        raise ValueError('Controller-only function lacks complete native local-closure proof')
    after=native.snapshot(board)
    if before['footprints']!=after['footprints'] or any(after['tracks'].get(u)!=q for u,q in before['tracks'].items()):
        raise ValueError('Controller-local closure changed retained source')
    _check(stop)
    proof.update(accepted=True,local_closed=closed,added_ids=sorted(set(after['tracks'])-set(before['tracks'])),
        external_obligations={net:row['external_pad_ids'] for net,row in subsets.items() if row['external_pad_ids']})
    return proof



def _controller_local_ownership(board,cfg,before,proof,*,deadline):
    from .collector_preparation import verified_controller_local_ownership
    return verified_controller_local_ownership(board,cfg,before,proof,deadline=deadline)


def _prefer_component_corridors(board,cfg,batch,*,deadline,valid_until):
    """A missing optional template cannot veto a certified ordinary collector."""
    from .controller_corridor_plan import plan_controller_corridors,CorridorUnavailable
    before=_proof_tools().snapshot(board);model=_digest(cfg)
    try:
        plan=plan_controller_corridors(board,cfg,batch,deadline=deadline,valid_until=valid_until)
        return plan,deepcopy(plan.receipt)
    except CorridorUnavailable as error:
        if _proof_tools().snapshot(board)!=before or _digest(cfg)!=model:
            raise ValueError('Rejected optional controller plan changed source state') from error
        batch.verify(board,cfg,deadline=valid_until-8)
        return None,dict(accepted=False,fallback='verified-collector',reason=str(error),source_batch_sha256=batch.seal)


def prepare_unified_sources(board,cfg,*,controller_refs,matrix_nets,local_closed,deadline,exit_bounds_nm=None,coherent_via_banks=False,receipt=None,component_corridors=False,corridor_valid_until=None):
    """One real all-function source selection followed by required local closure."""
    from .controller_ports import route_controller_ports
    from .interface_contract import mcu_signal_nets
    if type(component_corridors) is not bool:raise ValueError('Component corridors must be boolean')
    if component_corridors:
        from .generation import current_generation_profile
        if current_generation_profile().name!='dsn-process-v2':raise ValueError('Component corridors require explicit v2 generation')
        cfg=deepcopy(cfg)
        cfg['routing_preparation']=dict(mode='ordered-banks-v2',generation_profile=current_generation_profile().name,
            coherent_via_banks=True,unified_controller_plan=True,component_corridors=True)
        coherent_via_banks=True
    names=mcu_signal_nets(cfg);matrix=set(matrix_nets)
    receipt={} if receipt is None else receipt
    receipt.update(accepted=False,stage='source_selection',matrix_nets=sorted(matrix),stage_deadline=deadline)
    try:
        outstanding=names-set((local_closed or {}).get('nets',{}))
        # The template preference has its old short cap. Its genuine miss
        # leaves the rest of the SAME preparation deadline to fallback.
        source_stop=deadline-20;stock_stop=min(source_stop,time.monotonic()+35);_check(stock_stop)
        before=_proof_tools().snapshot(board)
        source=route_controller_ports(board,controller_refs=controller_refs,mcu_refs={'U_MCU'},plane_nets=cfg['plane_nets'],
            usb_nets={'USB_DM','USB_DP'},include_nets=outstanding,access_mode='prefer_same_layer',continuation_nm=500000,
            deadline=stock_stop,clearance_nm=max(board.GetDesignSettings().m_MinClearance,173000),
            coherent_via_banks=coherent_via_banks,exit_bounds_nm={'U_MCU':exit_bounds_nm} if exit_bounds_nm is not None else None)
        if not source['accepted']:
            from .generation import current_generation_profile
            if current_generation_profile().name=='dsn-process-v2' and source_stop-time.monotonic()>5:
                from .controller_escape import route_controller_escapes
                receipt['stock_escapes']=deepcopy(source)
                source=route_controller_escapes(board,controller_refs=controller_refs,mcu_refs={'U_MCU'},include_nets=outstanding,
                    exit_bounds_nm={'U_MCU':exit_bounds_nm} if exit_bounds_nm is not None else None,
                    deadline=source_stop,clearance_nm=max(board.GetDesignSettings().m_MinClearance,173000))
                if coherent_via_banks:
                    from .source_policy import legacy_fallback_provenance
                    source=legacy_fallback_provenance(source,receipt['stock_escapes'])
        receipt['source']=source
        batch=certify_source_batch(board,cfg,before,source,matrix_nets=matrix,requested_nets=outstanding,deadline=min(deadline-18,source_stop+2))
        corridor_plan=None;assignment=None
        if component_corridors:
            from smilepcb.keyboard_phases import ordered_nets
            from .collector_assignment import collector_assignment
            from .controller_corridor_plan import plan_controller_corridors
            phases=dict(ordered_nets(cfg));families={n:r for r in ('rows','columns') for n in phases[r]}
            matrix_ports=[deepcopy(q) for q in batch.ports if q['net'] in matrix]
            mapping,plans=collector_assignment(matrix_ports,_corridor_assignment_targets(board,cfg,batch),families)
            cfg,batch,assignment=rebind_source_batch(board,cfg,batch,net_mapping=mapping,deadline=deadline-18)
            assignment.update(strategy='early-collector-relative-v1',collector_plans=plans)
            receipt['assignment']=deepcopy(assignment)
            receipt.update(stage='component_corridors',source_batch_sha256=batch.seal)
            corridor_plan,receipt['component_corridors']=_prefer_component_corridors(board,cfg,batch,deadline=deadline-18,valid_until=max(deadline,corridor_valid_until or deadline))
        matrix_ports=[deepcopy(q) for q in batch.ports if q['net'] in matrix]
        receipt.update(stage='controller_local',source_batch_sha256=batch.seal)
        local_before=_proof_tools().snapshot(board)
        with corridor_plan.reserve(board,cfg,deadline=deadline) if corridor_plan is not None else nullcontext():
            local=close_controller_obligations(board,cfg,names-matrix,matrix_ports=matrix_ports,deadline=deadline)
        local_ownership=_controller_local_ownership(board,cfg,local_before,local,deadline=deadline)
        fixed=[q for q in batch.fixed_ports if q['net'] not in local['local_closed']['nets']]
        receipt.update(accepted=True,stage='matrix_handoff',source_nets=sorted(outstanding),
            controller_local=local,external_obligations=local['external_obligations'],source_deadline=source_stop)
        handoff=batch.handoff();handoff.controller_ownership=local_ownership
        if corridor_plan is not None:
            handoff.corridor_plan=corridor_plan;handoff.corridor_assignment=deepcopy(assignment)
        return handoff,dict(accepted=True,ports=fixed,local_closed=local['local_closed']),receipt
    except (ValueError,TimeoutError) as error:
        receipt['reason']=str(error);raise


def _corridor_assignment_targets(board,cfg,batch):
    # Only assignment uses centroids. The corridor planner independently binds
    # every actual numbered target pad and chooses a real physical endpoint.
    from .controller_corridor_plan import _backend
    targets=_backend().context(board,cfg,batch.ports)['targets']
    return {net:tuple(sum(q['point'][i] for q in rows)/len(rows) for i in (0,1))
        for net,rows in targets.items()}
