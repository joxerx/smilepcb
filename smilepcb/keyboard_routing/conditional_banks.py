"""Transactional complete matrix banks on the current generated native frame.

A current-generation ownership certificate is mandatory for replacement. Search
runs on isolated scratch boards; only a complete, reloaded matrix can be committed.
Bank ordering is a geometry/load heuristic, never archived routing evidence.
"""
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
import tempfile
import time

from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_phases import ordered_nets
from .generation import current_generation_profile
from .native_graph import copper_graph,route_components
from .feeder_repair import _isolated_editable_components,_pad_state
from .local_router import LocalRouter,vec
from .matrix import KeyboardRouter
from .interface_launches import reserve_launches,current_launches,interface_identity
from .conditional_geometry import (emit_shapes,FrozenProofRouter,saved_via_clear_cached,certify_pair,
                                   copy_native_tracks,prove_copper as _prove_copper)
from .conditional_sources import geometry_signature,SourceSearchBudget
from .conditional_search import conditional_search,derive_cold_bank_order,failed_first_order
from .free_feeder_plan import fallback_cutoffs,best_accepted_prefix
from .free_feeders import route_free_feeders
from .free_feeder_admission import closed_partial_shapes,prove_free_matrix,prove_free_seed


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class _BankRepairLedger:
    """One local allowance per physical bank, shared across source/prefix trials."""
    def __init__(self,banks,extra_seconds):
        import math
        if type(extra_seconds) not in (int,float) or not math.isfinite(extra_seconds) or extra_seconds<0:
            raise ValueError('Bank repair needs a finite nonnegative extra allowance')
        self.allowance_s=min(120.,extra_seconds/len(banks)) if banks else 0.
        self.spent_s={bank:0. for bank in banks}

    def remaining(self,bank):
        return max(0.,self.allowance_s-self.spent_s[bank])

    def admit(self,bank,now,ordinary_stop,shared_deadline):
        end=min(shared_deadline-8,now+self.remaining(bank))
        return end if end>ordinary_stop and end-now>3 else None

    def charge(self,bank,started,finished):
        elapsed=max(0.,finished-started);self.spent_s[bank]+=elapsed
        return {'elapsed_s':elapsed,'spent_s':self.spent_s[bank],'remaining_s':self.remaining(bank)}


def _journaled_feeder_callers(board,journal,native_route):
    """Observe normal indexed emission without changing the ordinary grid loop."""
    from .owned_feeder_repair import feeder_router,record_feeder_route
    def constructor(current,*,deadline,compact,track_width_nm):
        if current is not board or not compact:
            raise ValueError('Journaled feeder constructor changed its native parent')
        return feeder_router(current,journal,deadline=deadline)
    def connect(current,router,nets,**kwargs):
        if current is not board or len(nets)!=1:
            raise ValueError('Journaled feeder call needs one current bank net')
        net=next(iter(nets))
        def skipped():
            router.graph_diagnostic={'attempts':[],'skipped':True,
                'budget_exhausted':'Feeder deadline exhausted before routing'}
            return 0
        try:router.begin_route(net)
        except TimeoutError:
            if router.expired():return skipped()
            raise
        if router.expired():return skipped()
        before=snapshot(current);mark=len(router.added)
        if router.expired():return skipped()
        count=native_route(current,router,nets,**kwargs)
        # A route exception is deliberately not converted into a journal delta.
        record_feeder_route(current,journal,before,router.added[mark:],net)
        return count
    return constructor,connect


def _recover_owned_feeders(board,journal,feeder_ports,state,*,deadline):
    """Try actual missing functions; only the caller can certify the full bank."""
    from .owned_feeder_repair import repair_owned_feeders
    order=[q['net'] for q in feeder_ports];selected=set(journal.bank_nets)
    if len(order)!=len(set(order)) or set(order)!=selected:
        raise ValueError('Repair changed the complete physical bank order')
    history=[];seen=set();initial_deficit=sum(state['missing'].values())
    constructor,connect=_journaled_feeder_callers(board,journal,route_components)
    reason='finite_target_portfolio_exhausted'
    for _ in range(initial_deficit+1):
        before_round=sum(state['missing'].values())
        for target in order:
            if target not in state['missing']:continue
            if time.monotonic()>=deadline:
                reason='repair_budget_exhausted';break
            key=(target,tuple(sorted(state['missing'].items())),geometry_signature(journal.owners))
            if key in seen:continue
            seen.add(key)
            row={'target':target,'missing_before':deepcopy(state['missing']),'improved':False};history.append(row)
            # Retired/replaced geometry never lends witnesses to a new route.
            journal.witnesses[target]={}
            router=constructor(board,deadline=min(deadline,time.monotonic()+5),compact=True,
                track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000)
            router.grid_step=125000;router.search_budget_s=4;router.connection_budget_s=4
            router.search_expansion_limit=200000;router.heuristic_weight=3;router.via_cost_nm=1000000;router.layer_change_heuristic_nm=0
            router.routing_penalty=None
            if router.expired():
                row['reason']='observed_route_setup_expired'
                router=None
                continue
            began=time.monotonic()
            count=connect(board,router,{target},finer_grid_steps=())
            row['route']={'connections':count,'elapsed_s':time.monotonic()-began,
                'diagnostic':router.graph_diagnostic,'witness_count':len(journal.witnesses.get(target,{})),
                'query_omissions':getattr(router,'query_omissions',0)}
            router=None
            if time.monotonic()>=deadline:
                reason='repair_budget_exhausted';break
            try:state=_pad_state(copper_graph(board,selected,deadline),selected)
            except TimeoutError:
                row['reason']='native_assessment_deadline';reason='repair_budget_exhausted';break
            row['missing_after_route']=deepcopy(state['missing'])
            if target in state['missing']:
                outcome=repair_owned_feeders(board,journal,target,deadline=deadline)
                row['repair']={'improved':outcome.improved,'reason':outcome.reason,
                    'before_missing':outcome.before_missing,'after_missing':outcome.after_missing,
                    'retired_count':len(outcome.retired_ids),'added_count':len(outcome.added_shapes),
                    'attempts':list(outcome.attempts)}
                if time.monotonic()>=deadline:
                    reason='repair_budget_exhausted';break
                try:state=_pad_state(copper_graph(board,selected,deadline),selected)
                except TimeoutError:
                    row['reason']='native_assessment_deadline';reason='repair_budget_exhausted';break
                if outcome.improved and state['missing']!=outcome.after_missing:
                    raise ValueError('Owned repair result differs from the current native bank')
            row['missing_after']=deepcopy(state['missing'])
            row['improved']=sum(state['missing'].values())<sum(row['missing_before'].values())
        if not state['missing']:
            reason='native_bank_closed';break
        if time.monotonic()>=deadline:
            reason='repair_budget_exhausted';break
        if sum(state['missing'].values())>=before_round:
            reason='no_strict_native_progress';break
    return {'history':history,'missing':deepcopy(state['missing']),'missing_basis':'last_completed_native_graph','reason':reason}


def _matrix_nets(cfg):
    phases=dict(ordered_nets(cfg));return set(phases['rows']+phases['columns'])


@dataclass(frozen=True)
class GeneratedMatrixHeads:
    profile: str
    tracks: dict
    pads: dict
    ports: tuple
    model_sha256: str
    physical_sha256: str
    ownership_sha256: str


def _source_physics(board,ids):
    from smilepcb.copper_obstacles import own_clearance
    rows=[];minimum=board.GetDesignSettings().m_MinClearance
    for item in [*board.GetTracks(),*(d for f in board.GetFootprints() for d in f.Pads())]:
        uid=item.m_Uuid.AsString()
        if uid not in ids:continue
        for face in (p.F_Cu,p.B_Cu):
            if item.IsOnLayer(face):rows.append((uid,face,item.GetLocalClearance(),own_clearance(item,face,minimum),item.GetEffectiveShape(face).Format(False)))
        if isinstance(item,p.PCB_VIA):rows.append((uid,'hole',item.GetEffectiveHoleShape().Format(False)))
        elif isinstance(item,p.PAD):
            drill=item.GetDrillSize()
            if drill.x and drill.y:rows.append((uid,'hole',item.GetEffectiveHoleShape().Format(False)))
    return _digest(sorted(rows,key=repr))


def _representable_heads(board,ids):
    """Only the plain native generator recipe can enter serialized plans."""
    from .safety import prepare_added_via
    for item in board.GetTracks():
        if item.m_Uuid.AsString() not in ids:continue
        if isinstance(item,p.PCB_VIA):
            plain=p.PCB_VIA(board);plain.SetPosition(item.GetPosition());plain.SetWidth(item.GetWidth(p.F_Cu))
            plain.SetDrill(item.GetDrillValue());plain.SetViaType(p.VIATYPE_THROUGH);plain.SetLayerPair(p.F_Cu,p.B_Cu)
            plain.SetNet(board.FindNet(item.GetNetname()));prepare_added_via(plain)
            if item.GetViaType()!=p.VIATYPE_THROUGH or item.TopLayer()!=p.F_Cu or item.BottomLayer()!=p.B_Cu or item.Padstack()!=plain.Padstack():
                raise ValueError('Generated source has unrepresentable via properties')
        elif item.GetClass()=='PCB_TRACK':
            plain=p.PCB_TRACK(board);plain.SetStart(item.GetStart());plain.SetEnd(item.GetEnd());plain.SetLayer(item.GetLayer())
            plain.SetWidth(item.GetWidth());plain.SetNet(board.FindNet(item.GetNetname()))
        else:raise ValueError('Generated source has unrepresentable track properties')
        if item.GetLocalClearance()!=plain.GetLocalClearance():raise ValueError('Generated source has unrepresentable local clearance')
        for face in (p.F_Cu,p.B_Cu):
            if item.IsOnLayer(face)!=plain.IsOnLayer(face) or item.IsOnLayer(face) and item.GetEffectiveShape(face).Format(False)!=plain.GetEffectiveShape(face).Format(False):
                raise ValueError('Generated source has unrepresentable native geometry')


def certify_generated_heads(board,cfg,before,ports,*,deadline,source_batch=None):
    """Certify a fresh generated delta after its final GPIO net binding."""
    state=snapshot(board);nets=_matrix_nets(cfg);_model_parity(board,cfg)
    selected=deepcopy([q for q in ports if q['net'] in nets])
    if source_batch is not None:
        from .controller_plan import PreplannedSources
        if not isinstance(source_batch,PreplannedSources):raise ValueError('Internal shared source certificate required')
        delta=source_batch.matrix_delta(board,cfg,selected,deadline=deadline)
    else:delta=set(state['tracks'])-set(before['tracks'])
    owned=[u for q in selected for u in q['added_ids']]
    if (set(owned)!=delta or len(owned)!=len(set(owned)) or
            (source_batch is None and any(state['tracks'].get(u)!=shape for u,shape in before['tracks'].items()))):
        raise ValueError('Matrix heads differ from the exact fresh generated delta')
    _representable_heads(board,delta)
    actual={d['uuid']:d for f in state['footprints'].values() if f['ref']=='U_MCU'
            for d in f['pads'] if d['number'] and d['net'] in nets}
    claimed=[u for q in selected for u in q['pad_ids']]
    if set(claimed)!=set(actual) or len(claimed)!=len(set(claimed)):
        raise ValueError('Matrix heads omit or duplicate actual MCU physical pads')
    graph=copper_graph(board,nets,deadline);ids={q['item'].m_Uuid.AsString():i for i,q in enumerate(graph['nodes'])}
    for q in selected:
        if not q['added_ids'] or not q['pad_ids'] or any(actual[u]['net']!=q['net'] for u in q['pad_ids']):
            raise ValueError('Matrix head has no exact bound source pads')
        if any(state['tracks'][u]['net']!=q['net'] or state['tracks'][u]['locked'] or
               state['tracks'][u]['class'] not in ('PCB_TRACK','PCB_VIA') for u in q['added_ids']):
            raise ValueError('Matrix head claims unrelated, locked or unsupported copper')
        allowed=set(q['pad_ids'])|set(q['added_ids'])
        root=graph['find'](ids[q['pad_ids'][0]])
        members={n['item'].m_Uuid.AsString() for i,n in enumerate(graph['nodes']) if graph['find'](i)==root}
        if members!=allowed:raise ValueError('Fresh generated matrix head component is not exact owned copper and pads')
    if time.monotonic()>=deadline:raise TimeoutError('Matrix head ownership deadline')
    tracks={u:deepcopy(state['tracks'][u]) for u in owned}
    seal={'tracks':tracks,'pads':actual,'ports':selected}
    return GeneratedMatrixHeads(current_generation_profile().name,tracks,deepcopy(actual),tuple(selected),_digest(cfg),_source_physics(board,set(owned)|set(actual)),_digest(seal))


def _validate_ownership(board,cfg,ownership,ports,field):
    if not isinstance(ownership,GeneratedMatrixHeads):raise ValueError('Generated matrix ownership certificate is required')
    if ownership.profile!=current_generation_profile().name or ownership.model_sha256!=_digest(cfg):
        raise ValueError('Generated matrix ownership profile or model changed')
    seal={'tracks':ownership.tracks,'pads':ownership.pads,'ports':list(ownership.ports)}
    if _digest(seal)!=ownership.ownership_sha256:raise ValueError('Generated matrix ownership certificate was modified')
    state=snapshot(board);pads={q['uuid']:q for f in state['footprints'].values() for q in f['pads']}
    if (any(state['tracks'].get(u)!=shape for u,shape in ownership.tracks.items()) or
            any(pads.get(u)!=shape for u,shape in ownership.pads.items())):
        raise ValueError('Generated matrix heads or pads changed after certification')
    _representable_heads(board,set(ownership.tracks))
    if _source_physics(board,set(ownership.tracks)|set(ownership.pads))!=ownership.physical_sha256:
        raise ValueError('Generated source native shapes or clearances changed')
    if list(ownership.ports)!=[q for q in ports if q['net'] in _matrix_nets(cfg)]:
        raise ValueError('Generated matrix source metadata changed after certification')
    if set(ownership.tracks)&set(field['owned_ids']):raise ValueError('Generated source and field ownership overlap')
    return state


def physical_banks(board,ports):
    """Union actual MCU-local pad sides, including aliases and via-source ports."""
    pads={d.m_Uuid.AsString():d for f in board.GetFootprints() for d in f.Pads() if d.GetNumber()}
    parent={(axis,sign):(axis,sign) for axis in (0,1) for sign in (-1,1)}
    def find(value):
        while parent[value]!=value:value=parent[value]
        return value
    groups=[]
    for q in ports:
        points=[];sides=set()
        for uid in q['pad_ids']:
            d=pads[uid]
            if d.GetParentFootprint().GetReference()!='U_MCU':raise ValueError('Matrix bank owns a non-MCU source pad')
            v=d.GetFPRelativePosition();point=(v.x,v.y);points.append(point)
            axis=0 if abs(v.x)>abs(v.y) else 1
            if not point[axis]:raise ValueError('MCU source pad has no physical package side')
            sides.add((axis,1 if point[axis]>0 else -1))
        if not sides:raise ValueError('Matrix bank has no physical source pads')
        anchor=min(sides)
        for side in sides:parent[find(side)]=find(anchor)
        groups.append((q,sides,tuple(sum(v[i] for v in points)/len(points) for i in (0,1))))
    # A logical function cannot be split between separately selected banks.
    net_side={}
    for q,sides,_ in groups:
        side=next(iter(sides))
        if q['net'] in net_side:parent[find(side)]=find(net_side[q['net']])
        else:net_side[q['net']]=side
    active=set().union(*(sides for _,sides,_ in groups)) if groups else set()
    components={root:tuple(sorted(s for s in active if find(s)==root)) for root in {find(s) for s in active}}
    labels={root:'bank-'+str(i) for i,root in enumerate(sorted(components,key=lambda r:components[r]))}
    banks={};side_sets={labels[root]:list(sides) for root,sides in components.items()};positions={}
    for q,sides,point in groups:
        label=labels[find(next(iter(sides)))];banks.setdefault(label,[]).append(deepcopy(q));positions[interface_identity(q)]=point
    for bank in banks:banks[bank].sort(key=lambda q:(positions[interface_identity(q)],interface_identity(q)))
    return banks,side_sets


def _retained(board,state):
    actual=snapshot(board)
    return actual['footprints']==state['footprints'] and all(actual['tracks'].get(u)==v for u,v in state['tracks'].items())


def _model_parity(board,cfg):
    parts={q['ref']:q for q in cfg['parts']}
    if any(parts.get(f.GetReference(),{}).get('nets',{}).get(d.GetNumber())!=d.GetNetname()
           for f in board.GetFootprints() for d in f.Pads() if d.GetNumber()):
        raise ValueError('Conditional matrix CAD/model binding changed')
    if any(cfg['layout']['gpio'][role]!=cfg['qmk']['matrix_pins'][role] for role in ('rows','cols')):
        raise ValueError('Conditional matrix firmware mapping differs')


def _future_heads(banks,bank,prefix,heads):
    """Reserve original sources only for banks absent from this exact prefix."""
    prior=[q['bank'] for q in prefix]
    if bank not in banks or bank in prior or len(prior)!=len(set(prior)) or not set(prior)<=banks.keys():
        raise ValueError('Invalid future source bank history')
    processed=set(prior)|{bank}
    ports=deepcopy([q for key,values in banks.items() if key not in processed for q in values])
    ids=[u for q in ports for u in q['added_ids']]
    other={u for key in processed for q in banks[key] for u in q['added_ids']}
    if len(ids)!=len(set(ids)) or set(ids)&other or not set(ids)<=heads.keys():
        raise ValueError('Future source ownership overlaps or is missing')
    shapes={u:deepcopy(heads[u]) for u in ids}
    if any(shapes[u]['net']!=q['net'] for q in ports for u in q['added_ids']):
        raise ValueError('Future source bindings differ from their interfaces')
    return ports,shapes


class _TemporaryHeads:
    """Scratch-only original sources, never current plan or feeder ownership."""
    def __init__(self,board,shapes,ports,physical_sha256):
        self.board=board;self.shapes=deepcopy(shapes);self.ports=deepcopy(ports)
        self.physical_sha256=physical_sha256;self.installed=False
        self.before=snapshot(board);self.frame=None
        ids={u for q in ports for u in q['pad_ids']}
        self.pads={q['uuid']:deepcopy(q) for f in self.before['footprints'].values() for q in f['pads'] if q['uuid'] in ids}
        if self.pads.keys()!=ids:raise ValueError('Future source pads are missing')
        self.source_ids=set(shapes)|ids

    def install(self,*,deadline):
        if time.monotonic()>=deadline:raise TimeoutError('Future source installation deadline')
        if self.installed or snapshot(self.board)!=self.before or self.shapes.keys()&self.before['tracks'].keys():
            raise ValueError('Future source installation changed its native parent')
        if self.shapes:
            self.installed=True
            emit_shapes(self.board,self.shapes)
        self.frame=snapshot(self.board)
        if self.frame!={**self.before,'tracks':self.before['tracks']|self.shapes}:
            raise ValueError('Future source installation changed retained geometry')
        if self.shapes and _source_physics(self.board,self.source_ids)!=self.physical_sha256:
            raise ValueError('Future source native properties changed on installation')
        if time.monotonic()>=deadline:raise TimeoutError('Future source installation deadline')

    def verify_ports(self):
        actual={interface_identity(q):q for q in current_launches()}
        if any(actual.get(interface_identity(q))!=q for q in self.ports):
            raise ValueError('Future source launch metadata changed')

    def retire(self,*,deadline=None):
        if not self.installed:return
        if deadline is not None and time.monotonic()>=deadline:raise TimeoutError('Future source retirement deadline')
        before=snapshot(self.board)
        pads={q['uuid']:q for f in before['footprints'].values() for q in f['pads']}
        if (any(before['tracks'].get(u)!=q for u,q in self.shapes.items()) or
                any(pads.get(u)!=q for u,q in self.pads.items()) or
                _source_physics(self.board,self.source_ids)!=self.physical_sha256):
            raise ValueError('Temporary future source geometry or binding changed')
        from types import SimpleNamespace
        seal=FrozenProofRouter._physical_state(SimpleNamespace(board=self.board),set(self.shapes))
        for item in list(self.board.GetTracks()):
            if item.m_Uuid.AsString() in self.shapes:self.board.Delete(item)
        self.board.BuildConnectivity()
        self.installed=False
        expected={**before,'tracks':{u:q for u,q in before['tracks'].items() if u not in self.shapes}}
        if snapshot(self.board)!=expected or FrozenProofRouter._physical_state(SimpleNamespace(board=self.board))!=seal:
            raise ValueError('Future source retirement changed retained native geometry')
        if deadline is not None and time.monotonic()>=deadline:raise TimeoutError('Future source retirement deadline')


def _mixed_source_heads(board,ports,heads,feedback,*,generate,arguments,deadline,source_budget,source_board):
    """Replace one failed current interface while retaining every other original.

    Every trial uses the same source deadline and search allowance. Only a fully
    proved source set survives; the caller still owes its complete bank proof.
    """
    from types import SimpleNamespace
    report={'accepted':False,'strategy':'mixed_single_interface','ports':[],'trials':[]}
    identities={interface_identity(q) for q in ports};failed={tuple(sorted(ids)) for ids in feedback}
    if not failed<=identities:raise ValueError('Mixed source feedback differs from certified interfaces')
    frame=snapshot(board);outer=deepcopy(list(current_launches()))
    seal=FrozenProofRouter._physical_state(SimpleNamespace(board=board))
    if set(heads)&frame['tracks'].keys():raise ValueError('Mixed trial requires a headless current bank')
    for target in [q for q in ports if interface_identity(q) in failed][:4]:
        if time.monotonic()>=deadline or source_budget.states_left<=0:
            report['reason']='Shared source deadline or selection budget exhausted';break
        identity=interface_identity(target)
        trial={'interface':list(identity),'net':target['net'],'accepted':False};report['trials'].append(trial)
        if source_budget.candidate_limit(identity,96)<=0:
            trial['reason']='Shared source candidate budget exhausted';continue
        retained=deepcopy([q for q in ports if interface_identity(q)!=identity])
        retained_shapes={u:heads[u] for q in retained for u in q['added_ids']}
        retained_ids=set(retained_shapes)|{u for q in retained for u in q['pad_ids']}
        original_physics=_source_physics(source_board,retained_ids)
        old_target=geometry_signature({u:heads[u] for u in target['added_ids']})
        keep=False;local=None;graph=None
        try:
            with reserve_launches(outer):
                local=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000)
                local.refresh_interfaces(copper_graph(board,{q['net'] for q in outer+ports},deadline))
                admission=_admit_heads(local,retained_shapes,retained,deadline=deadline)
            trial['retained_admission']=admission
            local=None
            if not admission['accepted']:
                trial['reason']='retained_original_head_admission_rejected';continue
            emit_shapes(board,retained_shapes)
            if snapshot(board)!={**frame,'tracks':frame['tracks']|retained_shapes} or _source_physics(board,retained_ids)!=original_physics:
                raise ValueError('Mixed source retained original native geometry changed')
            if time.monotonic()>=deadline:raise TimeoutError('Shared source deadline exhausted')
            with reserve_launches(outer+retained):
                generated=generate(board,**arguments,include_nets={target['net']},deadline=deadline,source_budget=source_budget,
                    head_constraint={'original_geometry':old_target,'unchanged_heads':{identity:old_target}})
                if list(current_launches())!=outer+retained:raise ValueError('Mixed generator changed retained launch metadata')
            trial['head_generation']=generated
            if not generated['accepted']:
                trial['reason']=generated.get('reason','source_generation_failed');continue
            replacement=generated['ports']
            if len(replacement)!=1 or interface_identity(replacement[0])!=identity or replacement[0]['net']!=target['net']:
                raise ValueError('Mixed generator omitted or changed a complete physical interface')
            mixed=[deepcopy(replacement[0] if interface_identity(q)==identity else q) for q in ports]
            now=snapshot(board);shapes={u:q for u,q in now['tracks'].items() if u not in frame['tracks']}
            owned=[u for q in mixed for u in q['added_ids']]
            if len(owned)!=len(set(owned)) or set(owned)!=set(shapes) or any(shapes[u]['net']!=q['net'] for q in mixed for u in q['added_ids']):
                raise ValueError('Mixed source ownership differs from native emission')
            if (now!={**frame,'tracks':frame['tracks']|shapes} or
                    any(shapes.get(u)!=shape for u,shape in retained_shapes.items()) or
                    _source_physics(board,retained_ids)!=original_physics or
                    FrozenProofRouter._physical_state(SimpleNamespace(board=board),set(shapes))!=seal):
                raise ValueError('Mixed source changed retained originals or its native parent')
            changed=geometry_signature({u:shapes[u] for u in replacement[0]['added_ids']})
            if changed==old_target or geometry_signature(shapes)==geometry_signature(heads):
                raise ValueError('Mixed source target did not change physical geometry')
            if time.monotonic()>=deadline:raise TimeoutError('Shared source deadline exhausted')
            covered={q['net'] for q in outer+mixed};graph=copper_graph(board,covered,deadline)
            ids={node['item'].m_Uuid.AsString():i for i,node in enumerate(graph['nodes'])}
            for q in mixed:
                owned=q['pad_ids']+q['added_ids']
                if q['terminal_id'] not in q['added_ids'] or any(u not in ids for u in owned):
                    raise ValueError('Mixed source terminal coverage differs')
                if any(graph['find'](ids[u])!=graph['find'](ids[q['terminal_id']]) for u in owned):
                    raise ValueError('Mixed source pads do not reach their native terminal')
            # Saved source interiors are not new feeder first contacts. Prove
            # each head against all physical copper and every OTHER live guard;
            # saved via checking excludes only its own indexed UUID exactly.
            checked=0
            try:
                for q in mixed:
                    with reserve_launches(outer+[other for other in mixed if interface_identity(other)!=interface_identity(q)]):
                        checked+=_prove_copper(board,{u:shapes[u] for u in q['added_ids']},graph,covered,deadline)
            except ValueError as error:
                if str(error) not in ('Conditional matrix via violates creation oracle','Conditional matrix track violates creation oracle'):raise
                trial['reason']='mixed_head_or_launch_admission_rejected: '+str(error);continue
            if checked!=len(shapes):raise ValueError('Mixed source native proof omitted owned copper')
            trial['mixed_checked_items']=checked
            if time.monotonic()>=deadline:raise TimeoutError('Shared source deadline exhausted')
            trial['accepted']=True;keep=True
            report.update(accepted=True,ports=mixed,selected_interface=list(identity),selected_net=target['net'])
            return report
        except TimeoutError as error:
            trial['reason']=str(error);report['reason']=str(error);break
        finally:
            local=None;graph=None
            if not keep:
                for item in list(board.GetTracks()):
                    if item.m_Uuid.AsString() not in frame['tracks']:board.Delete(item)
                board.BuildConnectivity()
                if snapshot(board)!=frame or FrozenProofRouter._physical_state(SimpleNamespace(board=board))!=seal:
                    raise ValueError('Mixed source rollback changed its native parent')
            if list(current_launches())!=outer:raise ValueError('Mixed source trial changed outer launch metadata')
    report.setdefault('reason','Bounded mixed source trials exhausted')
    return report


def _admit_heads(router,shapes,ports,*,deadline):
    owners={u:q for q in ports for u in q['added_ids']};failed=[]
    if set(shapes)!=set(owners):raise ValueError('Original head ownership is incomplete')
    for uid,q in shapes.items():
        if time.monotonic()>=deadline:raise TimeoutError('Original head admission deadline')
        if q['class']=='PCB_TRACK':clear=router.clear(p.SHAPE_SEGMENT(vec(q['start']),vec(q['end']),q['width']),q['layer'],q['net'])
        elif q['class']=='PCB_VIA':
            width,drill=router.via_width,router.drill
            try:
                router.via_width=q['width'];router.drill=q['drill'];clear=router.via_clear(tuple(q['start']),q['net'])
            finally:router.via_width,router.drill=width,drill
        else:raise ValueError('Unsupported original source operation')
        if time.monotonic()>=deadline:raise TimeoutError('Original head admission deadline')
        if not clear:failed.append({'pad_ids':sorted(owners[uid]['pad_ids']),'shape_id':uid,'kind':'prefix_creation_oracle'})
    plans=[{'id':q['net'],'bank':q['net'],'shapes':{u:shapes[u] for u in q['added_ids']},
            'proof':{'geometry_sha256':geometry_signature({u:shapes[u] for u in q['added_ids']})}} for q in ports]
    for a,b in combinations(plans,2):
        check=certify_pair(a,b,base_sha256='head-admission',profile=router.generation.name,
            minimum_clearance=router.minimum_clearance,net_clearances=router.net_clearances,
            hole_clearance=router.generation.via_to_hole_clearance_nm,deadline=deadline)
        if not check['clear']:
            for q in ports:
                if q['net'] in (a['id'],b['id']):failed.append({'pad_ids':sorted(q['pad_ids']),'kind':'planned_head_pair','witness':check})
    return {'accepted':not failed,'failed_interfaces':[list(q) for q in sorted({tuple(v['pad_ids']) for v in failed})],'witnesses':failed}


def prepare_conditional_banks(board,cfg,field,ports,ownership,*,deadline,exit_bounds_nm=None,repair_extra_seconds=0.):
    """Try complete conditional banks inside the caller's existing deadline.

    A valid but unsupported multi-interface function is explicitly reported and
    left to the existing route. Invalid/stale certificates raise without edits.
    """
    from .source_policy import source_policy_arguments
    source_policy_arguments(cfg)  # Validate persisted opt-in before native mutation.
    started=time.monotonic();entry=_validate_ownership(board,cfg,ownership,ports,field)
    cutoffs=fallback_cutoffs(started,deadline,enabled=current_generation_profile().name=='dsn-process-v2')
    ordered_deadline=cutoffs['ordered_deadline']
    nets=_matrix_nets(cfg);members=deepcopy(list(ownership.ports));fixed=deepcopy([q for q in ports if q['net'] not in nets])
    if {q['net'] for q in members}!=nets or len(members)!=len(nets):
        return {'accepted':False,'supported':False,'reason':'multiple_or_missing_source_interfaces_per_function'}
    if board.GetCopperLayerCount()!=2:raise ValueError('Conditional matrix requires the declared two copper layers')
    if time.monotonic()>=deadline:raise TimeoutError('Conditional matrix setup deadline')
    _model_parity(board,cfg)
    field_ids=set(field['owned_ids']);heads=deepcopy(ownership.tracks);source_ids=set(heads)
    editable_field=set(field.get('generated_owned_ids',())) if cutoffs['enabled'] else set()
    if (not editable_field<=field_ids or not editable_field<=entry['tracks'].keys() or
            any(q['locked'] or q['net'] not in nets or q['class'] not in ('PCB_TRACK','PCB_VIA')
                for uid in editable_field for q in (entry['tracks'][uid],))):
        raise ValueError('Editable matrix field lacks fresh unlocked matrix copper ownership')
    required={d.m_Uuid.AsString():d.GetNetname() for f in board.GetFootprints() for d in f.Pads() if d.GetNumber() and d.GetNetname() in nets}
    banks,side_sets=physical_banks(board,members);order=derive_cold_bank_order(banks,side_sets)
    repair_ledger=_BankRepairLedger(banks,repair_extra_seconds)
    normal_router,normal_route=KeyboardRouter,route_components
    result={'accepted':False,'supported':True,'bank_order':order,'physical_banks':{b:[q['net'] for q in qs] for b,qs in banks.items()},
        'bank_sides':side_sets,'order_basis':'adjacent_local_pad_sides_combined_interface_load_heuristic',
        'budget':{'shared_deadline_remaining_s':max(0,deadline-started),'domain_seconds':25,'max_attempts':16,
            'repair_extra_seconds':repair_extra_seconds,'repair_per_bank_seconds':repair_ledger.allowance_s,
            'repair_spent_seconds':repair_ledger.spent_s,'repair_allowance_basis':'explicit_later_cutoff_local_physical_bank_ledger',
            'free_feeder_cutoffs':cutoffs},
        'attempts':[],'leaf_attempts':[],'combined_factor':'conditional complete-bank routing and certified unresolved physical-source feedback'}
    width=board.GetDesignSettings().m_TrackMinWidth or 150000
    retained={**entry,'tracks':{u:q for u,q in entry['tracks'].items() if u not in source_ids}}
    from types import SimpleNamespace
    rules=tuple(getattr(board.GetDesignSettings(),k) for k in ('m_MinClearance','m_HoleClearance','m_TrackMinWidth','m_CopperEdgeClearance','m_MinThroughDrill','m_ViasMinSize','m_ViasMinAnnularWidth'))
    physical_seal=FrozenProofRouter._physical_state(SimpleNamespace(board=board))
    base_sha=_digest({'frame':retained,'model':cfg,'fixed':fixed,'profile':current_generation_profile().name,'native_rules':rules,'physical_seal':physical_seal})
    def expected_pads(graph,selected):
        actual={q['item'].m_Uuid.AsString():q['net'] for q in graph['nodes'] if isinstance(q['item'],p.PAD) and q['item'].GetNumber() and q['net'] in selected}
        if actual!={u:n for u,n in required.items() if n in selected}:raise ValueError('Conditional matrix omitted or changed an actual terminal')
    def source_contacts(target,actual_ports,graph):
        state=snapshot(target);ids={q['item'].m_Uuid.AsString():i for i,q in enumerate(graph['nodes'])}
        original={q['net']:interface_identity(q) for q in members}
        if {q['net'] for q in actual_ports}!={q['net'] for q in members}:raise ValueError('Conditional matrix source coverage changed')
        for q in actual_ports:
            if interface_identity(q)!=original[q['net']]:raise ValueError('Conditional matrix physical alias inventory changed')
            owned=q['added_ids'];terminal=q.get('terminal_id') or q.get('via_id')
            if not owned or terminal not in owned or any(state['tracks'].get(u,{}).get('net')!=q['net'] for u in owned):
                raise ValueError('Conditional matrix source ownership or terminal binding changed')
            if any(u not in ids for u in [*q['pad_ids'],*owned]):raise ValueError('Conditional matrix source ownership lost native copper')
            if len({graph['find'](ids[u]) for u in [*q['pad_ids'],*owned]})!=1:raise ValueError('Conditional matrix source is disconnected from its owned terminal')
    def parent_signature(frame,actual_ports):return _digest({'frame':frame,'ports':actual_ports,'base_sha256':base_sha})
    # Scratch files contain reproducible native frames; plans and diagnostics are
    # returned in the placement receipt. No runtime dependency on lab artifacts.
    with tempfile.TemporaryDirectory(prefix='smilepcb-matrix-') as folder:
        folder=Path(folder);entry_path=folder/'entry.kicad_pcb';base_path=folder/'base.kicad_pcb';leaf_path=folder/'leaf.kicad_pcb'
        if not p.SaveBoard(str(entry_path),board):raise ValueError('Could not stage conditional matrix frame')
        def load(path):
            check=p.LoadBoard(str(path));project=board.GetProject()
            if project is not None:check.SetProject(project,True)
            check.SetDesignSettings(board.GetDesignSettings())
            return check
        initial=load(entry_path)
        if snapshot(initial)!=entry:raise ValueError('Scratch matrix frame changed on reload')
        # Native rules and per-item clearance are part of scratch equivalence.
        from smilepcb.copper_obstacles import own_clearance
        def clearances(target):
            return {(q.m_Uuid.AsString(),face):own_clearance(q,face,target.GetDesignSettings().m_MinClearance)
                for q in [*target.GetTracks(),*(d for f in target.GetFootprints() for d in f.Pads())]
                for face in (p.F_Cu,p.B_Cu) if q.IsOnLayer(face)}
        if clearances(initial)!=clearances(board):raise ValueError('Scratch matrix creation clearances differ')
        if FrozenProofRouter._physical_state(SimpleNamespace(board=initial))!=physical_seal:raise ValueError('Scratch native physical geometry differs')
        for item in list(initial.GetTracks()):
            if item.m_Uuid.AsString() in source_ids:initial.Delete(item)
        initial.BuildConnectivity()
        if snapshot(initial)!=retained:raise ValueError('Scratch source removal changed retained geometry or binding')
        retained_physics=FrozenProofRouter._physical_state(SimpleNamespace(board=initial))
        retained_rules=FrozenProofRouter._rule_state(SimpleNamespace(board=initial))
        if not p.SaveBoard(str(base_path),initial):raise ValueError('Could not stage headless conditional matrix frame')
        del initial
        def rebuild(prefix):
            check=load(base_path)
            if snapshot(check)!=retained:raise ValueError('Conditional matrix base changed')
            actual_ports=[];prior=[]
            for previous in prefix:
                before=snapshot(check)
                if previous['parent_sha256']!=parent_signature(before,actual_ports) or previous['conditional_on']!=prior:
                    raise ValueError('Conditional bank plan belongs to another native parent')
                emit_shapes(check,previous['shapes']);state=snapshot(check)
                if not _retained(check,before) or any(state['tracks'].get(u)!=q for u,q in previous['shapes'].items()):
                    raise ValueError('Conditional prefix changed native geometry or net bindings')
                actual_ports.extend(deepcopy(previous['ports']))
                prior.append({'bank':previous['bank'],'id':previous['id'],'geometry_sha256':previous['proof']['geometry_sha256']})
            _model_parity(check,cfg)
            return check,snapshot(check),actual_ports
        best_partial = None
        def attempt(bank,option,prefix,feedback,search_deadline):
            nonlocal best_partial
            if option not in ('original','original_priority','regenerated'):
                raise ValueError('Unknown conditional bank source/feeder option')
            tick=time.monotonic();stop=min(search_deadline-8,tick+25);qs=banks[bank];selected={q['net'] for q in qs}
            KeyboardRouter,route_components=normal_router,normal_route
            journal=None;repair_started=None;temporary=None;partial=None
            old_heads={u:heads[u] for q in qs for u in q['added_ids']};old_signature=geometry_signature(old_heads)
            row={'id':'attempt-%03d'%len(result['attempts']),'bank':bank,'option':option,'accepted':False,'failed_interfaces':[],
                'feedback_received':[list(q) for q in feedback],'routing_history':[],'stage':'load',
                'source_interfaces':[sorted(q['pad_ids']) for q in qs],'source_order':[q['net'] for q in qs]};plan=None;check=None
            if stop-tick<5:
                row.update(reason='global_budget_before_domain',elapsed_s=time.monotonic()-tick);result['attempts'].append(row);return row
            check,parent,prefix_ports=rebuild(prefix);prefix_nets={q['net'] for q in prefix_ports};covered=selected|prefix_nets|{q['net'] for q in fixed}
            row['parent_sha256']=parent_signature(parent,prefix_ports)
            try:
                future_ports,future_shapes=_future_heads(banks,bank,prefix,heads)
                if future_shapes:
                    # A stale/incompatible prefix cannot acquire future source
                    # ownership by force. Its failure is not current-bank feedback.
                    with reserve_launches(fixed+prefix_ports):
                        local=LocalRouter(check,deadline=stop,compact=True,track_width_nm=width)
                        local.refresh_interfaces(copper_graph(check,covered,stop))
                        admission=_admit_heads(local,future_shapes,future_ports,deadline=stop)
                        row['future_head_admission']=admission
                        if not admission['accepted']:raise ValueError('future_head_admission_rejected')
                future_ids=set(future_shapes)|{u for q in future_ports for u in q['pad_ids']}
                temporary=_TemporaryHeads(check,future_shapes,future_ports,_source_physics(board,future_ids))
                temporary.install(deadline=stop)
                with reserve_launches(fixed+prefix_ports+future_ports):
                    graph=copper_graph(check,covered,stop)
                    expected_pads(graph,prefix_nets)
                    if _pad_state(graph,prefix_nets)['missing']:raise ValueError('Accepted prefix lost physical closure')
                    local=LocalRouter(check,deadline=stop,compact=True,track_width_nm=width);local.refresh_interfaces(graph)
                    row['stage']='source_generation'
                    if option in ('original','original_priority'):
                        admission=_admit_heads(local,old_heads,qs,deadline=stop);row['head_admission']=admission
                        if not admission['accepted']:
                            row['failed_interfaces']=admission['failed_interfaces'];row['feedback_basis']='native_original_head_admission'
                            raise ValueError('original_head_admission_rejected')
                        emit_shapes(check,old_heads);bank_ports=deepcopy(qs)
                    else:
                        interface_nets={interface_identity(q):q['net'] for q in qs}
                        if any(tuple(sorted(ids)) not in interface_nets for ids in feedback):raise ValueError('Source feedback belongs to another physical interface')
                        old_per_net={q['net']:geometry_signature({u:old_heads[u] for u in q['added_ids']}) for q in qs}
                        constraint={'original_geometry':old_signature,'unchanged_heads':{
                            tuple(sorted(ids)):old_per_net[interface_nets[tuple(sorted(ids))]] for ids in feedback}}
                        from .controller_ports import route_controller_ports
                        source_end=min(stop-5,time.monotonic()+10)
                        source_budget=SourceSearchBudget([q['pad_ids'] for q in qs])
                        row.update(source_strategy='full_regeneration',source_deadline=source_end)
                        source_frame=snapshot(check)
                        source_seal=FrozenProofRouter._physical_state(SimpleNamespace(board=check))
                        from .source_policy import source_policy_arguments
                        arguments={'controller_refs':{q['ref'] for q in cfg['parts'] if q['region']=='controller' and not q['ref'].startswith('#')},
                            'mcu_refs':{'U_MCU'},'plane_nets':cfg['plane_nets'],
                            'access_mode':'prefer_same_layer','continuation_nm':500000,
                            'clearance_nm':max(check.GetDesignSettings().m_MinClearance,173000),
                            'exit_bounds_nm':{'U_MCU':exit_bounds_nm} if exit_bounds_nm is not None else None,**source_policy_arguments(cfg)}
                        local=graph=None
                        generated=route_controller_ports(check,**arguments,include_nets=selected,
                            deadline=source_end,head_constraint=constraint,source_budget=source_budget)
                        row['head_generation']=generated
                        if (not generated['accepted'] and feedback and not generated.get('budget_exhausted') and
                                generated.get('reason') in ('No legal outward interface candidates','No mutually clear short interfaces within the candidate set')):
                            # The ordinary generator owns cleanup of its additions.
                            # A violated frame cannot grant a mixed trial license.
                            check.BuildConnectivity()
                            if snapshot(check)!=source_frame or FrozenProofRouter._physical_state(SimpleNamespace(board=check))!=source_seal:
                                raise ValueError('Full source generation did not restore its native parent')
                            mixed=_mixed_source_heads(check,qs,old_heads,feedback,generate=route_controller_ports,
                                arguments=arguments,deadline=source_end,source_budget=source_budget,source_board=board)
                            mixed['trigger_reason']=generated['reason'];row['mixed_head_generation']=mixed
                            if mixed['accepted']:
                                generated=mixed;row['source_strategy']='mixed_single_interface'
                        row['source_search_budget']=source_budget.receipt()
                        if not generated['accepted']:raise ValueError('source_generation_failed: '+generated.get('reason','unknown'))
                        bank_ports=generated['ports']
                    if {q['net'] for q in bank_ports}!=selected or len(bank_ports)!=len(selected):raise ValueError('Source generation omitted or duplicated an interface')
                    now=snapshot(check);new_heads={u:q for u,q in now['tracks'].items() if u not in temporary.frame['tracks']}
                    if not _retained(check,parent):raise ValueError('Source emission changed prefix geometry or bindings')
                    if option in ('original','original_priority') and any(now['tracks'].get(u)!=q for u,q in old_heads.items()):raise ValueError('Original source bindings changed during emission')
                    if {u for q in bank_ports for u in q['added_ids']}!=set(new_heads) or any(new_heads[u]['net']!=q['net'] for q in bank_ports for u in q['added_ids']):
                        raise ValueError('Generated head ownership differs from native emission')
                    row['head_geometry_sha256']=geometry_signature(new_heads)
                    row['head_interface_signatures']={','.join(interface_identity(q)):geometry_signature({u:new_heads[u] for u in q['added_ids']}) for q in bank_ports}
                    if option=='regenerated':
                        if row['head_geometry_sha256']==old_signature:raise ValueError('Regenerated source set did not change geometry')
                        changed_feedback=feedback if row['source_strategy']=='full_regeneration' else [generated['selected_interface']]
                        for ids in changed_feedback:
                            if row['head_interface_signatures'][','.join(sorted(ids))]==old_per_net[interface_nets[tuple(sorted(ids))]]:
                                raise ValueError('Certified unresolved physical source did not change geometry')
                        if row['source_strategy']=='mixed_single_interface':
                            changed=tuple(sorted(generated['selected_interface']))
                            for q in qs:
                                if interface_identity(q)==changed:continue
                                if (next(p for p in bank_ports if interface_identity(p)==interface_identity(q))!=q or
                                        any(new_heads.get(u)!=old_heads[u] for u in q['added_ids'])):
                                    raise ValueError('Mixed source changed another original interface')
                    with reserve_launches(bank_ports+fixed+prefix_ports+future_ports):
                        local=LocalRouter(check,deadline=stop,compact=True,track_width_nm=width)
                        local.refresh_interfaces(copper_graph(check,covered,stop))
                        row['stage']='complete_bank_routing'
                        from .collectors import route_collectors
                        row['collectors']=route_collectors(check,cfg,bank_ports,field_owned_ids=field_ids,deadline=min(stop-3,time.monotonic()+6))
                        if repair_ledger.remaining(bank)>0:
                            from .owned_feeder_repair import begin_feeders
                            journal=begin_feeders(check,selected)
                            KeyboardRouter,route_components=_journaled_feeder_callers(check,journal,normal_route)
                        # The original head emission, launch inventory and collector
                        # inputs keep their physical order. Only subsequent feeder
                        # iteration responds to this parent's certified missing set.
                        feeder_ports=qs
                        if option=='original_priority':
                            prioritized=failed_first_order(row['source_interfaces'],feedback)
                            if prioritized is None:raise ValueError('Unsupported or unchanged same-head feeder priority')
                            by_interface={interface_identity(q):q for q in qs}
                            feeder_ports=[by_interface[key] for key in prioritized]
                        row['feeder_order']=[q['net'] for q in feeder_ports]
                        row['feeder_interfaces']=[sorted(q['pad_ids']) for q in feeder_ports]
                        row['feeder_order_basis']='certified_original_missing_interfaces_first' if option=='original_priority' else 'physical_source_order'
                        for grid,allowance in ((500000,2),(125000,4)):
                            if time.monotonic()>=stop-3:break
                            router=None
                            state=_pad_state(copper_graph(check,selected,stop),selected)
                            for q in feeder_ports:
                                net=q['net']
                                if net not in state['missing']:continue
                                if time.monotonic()>=stop-3:break
                                route_deadline=min(stop-3,time.monotonic()+allowance+1)
                                # Collectors have finished advancing source heads.
                                # These feeders only append through indexed emitters.
                                if router is None:router=KeyboardRouter(check,deadline=route_deadline,compact=True,track_width_nm=width)
                                else:router.deadline=route_deadline
                                for name in ('last_search','last_plan','graph_diagnostic','attempt_counts'):
                                    if hasattr(router,name):delattr(router,name)
                                router.grid_step=grid;router.search_budget_s=allowance;router.connection_budget_s=allowance
                                router.search_expansion_limit=200000;router.heuristic_weight=3;router.via_cost_nm=1000000;router.layer_change_heuristic_nm=0
                                face=next(f.GetLayer() for f in check.GetFootprints() if f.GetReference()=='U_MCU')
                                router.routing_penalty=(lambda shape,layer,net,via:0 if via or layer==face else 2*shape.GetTotalLength()) if grid==500000 else None
                                began=time.monotonic()
                                if router.expired():
                                    count=0;router.graph_diagnostic={'attempts':[],'skipped':True,'budget_exhausted':'Feeder deadline exhausted before routing'}
                                else:count=route_components(check,router,{net},finer_grid_steps=(125000,) if grid==500000 else ())
                                row['routing_history'].append({'net':net,'grid_nm':grid,'connections':count,'elapsed_s':time.monotonic()-began,'diagnostic':router.graph_diagnostic})
                        if journal is not None:
                            # Admission uses the original bank proof window; no
                            # repair work spends its stop-3 ordinary routing tail.
                            graph=copper_graph(check,covered,stop);state=_pad_state(graph,selected)
                            expected_pads(graph,selected|prefix_nets)
                            if _pad_state(graph,prefix_nets)['missing']:raise ValueError('New bank lost prior closure')
                            if state['missing']:
                                began=time.monotonic();phase_end=repair_ledger.admit(bank,began,stop,search_deadline)
                                if phase_end is not None:
                                    repair_started=began;router=None
                                    row['owned_feeder_repair']={'allowance_before_s':repair_ledger.remaining(bank),
                                        'phase_seconds':phase_end-began,'missing_before':deepcopy(state['missing'])}
                                    stop=phase_end;row['stage']='owned_feeder_repair'
                                    row['owned_feeder_repair']['recovery']=_recover_owned_feeders(check,journal,feeder_ports,state,deadline=stop-3)
                        actual_ports=deepcopy([q for q in current_launches() if q['net'] in selected])
                        temporary.verify_ports()
                        if future_shapes:
                            # Certify the completed current delta while all future
                            # copper and live departures still constrain creation.
                            future_covered=covered|{q['net'] for q in future_ports}
                            graph=copper_graph(check,future_covered,stop)
                            if not _pad_state(graph,selected)['missing']:
                                current=snapshot(check)
                                added={u:q for u,q in current['tracks'].items() if u not in temporary.frame['tracks']}
                                if any(q['net'] not in selected for q in added.values()):raise ValueError('Conditional bank added unrelated copper')
                                row['future_reservation_checked_items']=_prove_copper(check,added,graph,future_covered,stop)
                        # No object indexed against the temporary native frame
                        # may route or prove after its exact sources are retired.
                        local=router=graph=journal=None
                        KeyboardRouter,route_components=normal_router,normal_route
                        temporary.retire(deadline=stop)
                with reserve_launches(actual_ports+fixed+prefix_ports):
                    row['stage']='complete_bank_proof';graph=copper_graph(check,covered,stop);state=_pad_state(graph,selected)
                    row['missing']=state['missing'];expected_pads(graph,selected|prefix_nets)
                    if repair_started is not None:row['owned_feeder_repair']['missing_after']=deepcopy(state['missing'])
                    if state['missing']:
                        row['failed_interfaces']=[sorted(q['pad_ids']) for q in qs if q['net'] in state['missing']]
                        row['feedback_basis']='completed_final_native_graph_after_scheduled_routing'
                        if cutoffs['enabled'] and time.monotonic()<stop:
                            # Save only the exact delta of fully connected functions.
                            # This cannot admit the bank or supply a plan to search.
                            try:
                                delta={uid:shape for uid,shape in snapshot(check)['tracks'].items()
                                       if uid not in parent['tracks']}
                                if any(shape['net'] not in selected or shape['locked'] or
                                       shape['class'] not in ('PCB_TRACK','PCB_VIA') for shape in delta.values()):
                                    raise ValueError('Partial bank has unowned or unsupported copper')
                                shapes=closed_partial_shapes(graph,required,delta,selected-set(state['missing']))
                                closed={shape['net'] for shape in shapes.values()}
                                score=len(prefix_nets)+len(closed)
                                if closed and (best_partial is None or score>best_partial['score']):
                                    _representable_heads(check,shapes)
                                    partial={'prefix':deepcopy(prefix),'parent_sha256':row['parent_sha256'],
                                             'attempt_id':row['id'],'shapes':shapes,'nets':closed,'score':score}
                            except (ValueError,TimeoutError) as error:
                                row['partial_seed_capture_reason']=str(error)
                        raise ValueError('complete_bank_routing_incomplete')
                    if _pad_state(graph,prefix_nets)['missing']:raise ValueError('New bank lost prior closure')
                    current=snapshot(check);added={u:q for u,q in current['tracks'].items() if u not in parent['tracks']}
                    if any(q['net'] not in selected for q in added.values()):raise ValueError('Conditional bank added unrelated copper')
                    row['creation_checked_items']=_prove_copper(check,added,graph,covered,stop)
                    from .safety import require_via_land_clearance
                    require_via_land_clearance(check,deadline=stop)
                    if not _retained(check,parent):raise ValueError('Conditional bank changed retained native frame')
                    actual_ports=deepcopy([q for q in current_launches() if q['net'] in selected])
                    plan={'id':row['id']+':'+option,'bank':bank,'parent_sha256':row['parent_sha256'],
                        'conditional_on':[{'bank':q['bank'],'id':q['id'],'geometry_sha256':q['proof']['geometry_sha256']} for q in prefix],
                        'shapes':added,'ports':actual_ports,'head_ids':sorted(new_heads),
                        'proof':{'geometry_sha256':geometry_signature(added),'base_sha256':base_sha,'profile':current_generation_profile().name}}
                    row.update(accepted=True,stage='complete_bank_accepted',added_count=len(added))
            except (ValueError,TimeoutError) as error:
                row['reason']=str(error)
                if isinstance(error,TimeoutError):row['failed_interfaces']=[];row['feedback_basis']='final_assessment_unavailable_due_deadline'
            finally:
                try:
                    if temporary is not None and temporary.installed:
                        try:temporary.retire()
                        except (ValueError,TimeoutError) as error:
                            row.update(accepted=False,reason='future_head_cleanup_failed: '+str(error),failed_interfaces=[]);plan=None
                    row['prefix_preserved']=_retained(check,parent)
                    if not row['prefix_preserved']:row.update(accepted=False,reason='candidate_changed_prefix',failed_interfaces=[]);plan=None
                    if repair_started is not None and time.monotonic()>=stop:
                        row.update(accepted=False,reason='owned_feeder_repair_deadline',failed_interfaces=[],
                            feedback_basis='final_assessment_unavailable_due_deadline');plan=None
                finally:
                    if repair_started is not None:
                        row['owned_feeder_repair'].update(repair_ledger.charge(bank,repair_started,time.monotonic()))
                    row['elapsed_s']=time.monotonic()-tick;result['attempts'].append(row)
            if (partial is not None and row['prefix_preserved'] and time.monotonic()<stop and
                    row.get('reason')=='complete_bank_routing_incomplete'):
                best_partial=partial
                row['partial_seed_candidate_nets']=sorted(partial['nets'])
            return dict(row,plan=plan if row['accepted'] else None)
        def leaf_validator(prefix):
            leaf={'accepted':False};tick=time.monotonic()
            if len(prefix)!=len(banks) or {q['bank'] for q in prefix}!=set(banks):raise ValueError('Partial banks cannot enter matrix admission')
            check,frame,actual_ports=rebuild(prefix);covered=nets|{q['net'] for q in fixed}
            try:
                graph=copper_graph(check,covered,ordered_deadline);expected_pads(graph,nets)
                state=_pad_state(graph,nets);leaf['matrix_groups']=state['groups']
                if state['missing']:raise ValueError('Conditional matrix leaf is incomplete')
                source_contacts(check,actual_ports,graph)
                added={u:q for u,q in frame['tracks'].items() if u not in retained['tracks']}
                if added!={u:q for plan in prefix for u,q in plan['shapes'].items()} or not _retained(check,retained):raise ValueError('Conditional matrix leaf ownership changed')
                with reserve_launches(actual_ports+fixed):
                    leaf['creation_checked_items']=_prove_copper(check,added,graph,covered,ordered_deadline)
                _model_parity(check,cfg)
                if not p.SaveBoard(str(leaf_path),check):raise ValueError('Could not save conditional matrix leaf')
                reloaded=load(leaf_path)
                if snapshot(reloaded)!=frame:raise ValueError('Conditional matrix leaf changed after reload')
                graph=copper_graph(reloaded,covered,ordered_deadline);expected_pads(graph,nets);source_contacts(reloaded,actual_ports,graph)
                if _pad_state(graph,nets)['missing']:raise ValueError('Saved conditional matrix lost physical closure')
                _model_parity(reloaded,cfg)
                leaf.update(accepted=True,ports=actual_ports,source_ids=sorted({u for q in actual_ports for u in q['added_ids']}),
                            head_ids=sorted({u for plan in prefix for u in plan['head_ids']}),saved_reload_exact=True)
            except (ValueError,TimeoutError) as error:leaf['reason']=str(error)
            finally:leaf['elapsed_s']=time.monotonic()-tick;result['leaf_attempts'].append(leaf)
            return leaf
        search=conditional_search(order,attempt,max_attempts=16,deadline=ordered_deadline,leaf_validator=leaf_validator,
            retry_unresolved_first=current_generation_profile().name=='dsn-process-v2')
        result['selection']=search
        intended=leaf=None;free=False
        commit_retained=retained
        retired_field=set()
        if search['accepted']:
            plans=search['selection'];intended={u:q for plan in plans for u,q in plan['shapes'].items()};leaf=search['leaf_result']
        elif cutoffs['enabled'] and time.monotonic()<cutoffs['routing_deadline']:
            # Rebuild only accepted ancestry, never the failed candidate's debris.
            # Allow local repair of its feeders and fresh field copper only.
            # The original seed proof still checks the entire field.
            tick=time.monotonic();fallback={'accepted':False,'partial_seed_nets':[]};result['free_feeders']=fallback
            try:
                prefix=best_accepted_prefix(search)
                check=None
                if best_partial is not None and best_partial['score']>sum(len(plan['ports']) for plan in prefix):
                    fallback['partial_seed_attempt']=best_partial['attempt_id']
                    try:
                        check,parent,prefix_ports=rebuild(best_partial['prefix'])
                        if parent_signature(parent,prefix_ports)!=best_partial['parent_sha256']:
                            raise ValueError('Partial seed belongs to another native parent')
                        shapes=best_partial['shapes']
                        if set(shapes)&set(parent['tracks']):raise ValueError('Partial seed overlaps retained ownership')
                        emit_shapes(check,shapes)
                        frame=snapshot(check)
                        if (not _retained(check,parent) or
                                {u:q for u,q in frame['tracks'].items() if u not in parent['tracks']}!=shapes):
                            raise ValueError('Partial seed changed during native replay')
                        seed_nets={q['net'] for q in prefix_ports}|best_partial['nets']
                        fallback['partial_seed_proof']=prove_free_seed(check,cfg,retained,seed_nets=seed_nets,
                            fixed_ports=fixed,physical_seal=retained_physics,rule_seal=retained_rules,
                            deadline=cutoffs['routing_deadline'])
                        prefix=best_partial['prefix']
                        fallback['partial_seed_nets']=sorted(best_partial['nets'])
                    except (ValueError,TimeoutError) as error:
                        fallback['partial_seed_rejected_reason']=str(error)
                        check=None
                if check is None:
                    check,frame,prefix_ports=rebuild(prefix)
                    seed_nets={q['net'] for q in prefix_ports}
                    graph=copper_graph(check,nets|{q['net'] for q in fixed},cutoffs['routing_deadline'])
                    expected_pads(graph,nets)
                    if _pad_state(graph,seed_nets)['missing']:raise ValueError('Free-feeder prefix lost native closure')
                fallback.update(seed_banks=[q['bank'] for q in prefix],seed_nets=sorted(seed_nets),
                                seed_copper_count=len(frame['tracks'])-len(retained['tracks']))
                free_retained={**retained,'tracks':{u:q for u,q in retained['tracks'].items() if u not in editable_field}}
                fallback['editable_field_count']=len(editable_field)
                fallback['routing']=route_free_feeders(check,cfg,free_retained,
                    groups=[[q['net'] for q in banks[bank]] for bank in order],fixed_ports=fixed,
                    deadline=cutoffs['routing_deadline'])
                frame=snapshot(check)
                retired_field=editable_field-frame['tracks'].keys()
                commit_retained={**retained,'tracks':{u:q for u,q in retained['tracks'].items() if u not in retired_field}}
                intended={u:q for u,q in frame['tracks'].items() if u not in commit_retained['tracks']}
                if fallback['routing'].get('remaining')=={}:
                    # Native routing can leave a detached proposal even when
                    # all pads are joined. Only drop wholly new components.
                    graph=copper_graph(check,nets,deadline)
                    orphan_ids=_isolated_editable_components(graph,intended.keys())
                    if orphan_ids:
                        for item in list(check.GetTracks()):
                            if item.m_Uuid.AsString() in orphan_ids:check.Delete(item)
                        check.BuildConnectivity()
                        frame=snapshot(check)
                        intended={u:q for u,q in frame['tracks'].items() if u not in commit_retained['tracks']}
                    fallback['retired_orphan_ids']=sorted(orphan_ids)
                    del graph
                _representable_heads(check,set(intended))
                # The live caller has the original raw properties. Seal every
                # unchanged object and require native creation proof for the delta.
                commit_physics=(FrozenProofRouter._physical_state(SimpleNamespace(board=board),
                    excluded_ids=source_ids|retired_field) if retired_field else retained_physics)
                def free_proof(target):
                    return prove_free_matrix(target,cfg,commit_retained,fixed_ports=fixed,
                        physical_seal=commit_physics,rule_seal=retained_rules,deadline=deadline)
                fallback['native_proof']=free_proof(check)
                if not p.SaveBoard(str(leaf_path),check):raise ValueError('Could not save free matrix leaf')
                reloaded=load(leaf_path)
                if snapshot(reloaded)!=frame:raise ValueError('Free matrix leaf changed after reload')
                fallback['reload_proof']=free_proof(reloaded)
                fallback['saved_reload_exact']=True
                leaf=dict(fallback['reload_proof'],ports=members,source_ids=[],head_ids=[])
                free=True
            except (ValueError,TimeoutError) as error:
                fallback['reason']=str(error);intended=leaf=None
            finally:fallback['elapsed_s']=time.monotonic()-tick
        if leaf is not None:
            if snapshot(board)!=entry:raise ValueError('Caller changed while conditional matrix search was isolated')
            removed_ids=source_ids|retired_field
            removed_copies=copy_native_tracks(board,removed_ids)
            try:
                for item in list(board.GetTracks()):
                    if item.m_Uuid.AsString() in removed_ids:board.Delete(item)
                emit_shapes(board,intended)
                actual=snapshot(board)
                if actual!={**commit_retained,'tracks':commit_retained['tracks']|intended}:
                    raise ValueError('Conditional matrix commit changed retained geometry or bindings')
                _model_parity(board,cfg)
                if free:
                    result['free_feeders']['commit_proof']=free_proof(board)
                else:
                    with reserve_launches(leaf['ports']+fixed):
                        graph=copper_graph(board,nets|{q['net'] for q in fixed},deadline);expected_pads(graph,nets);source_contacts(board,leaf['ports'],graph)
                        if _pad_state(graph,nets)['missing']:raise ValueError('Conditional matrix commit lost physical closure')
                result.update(accepted=True,matrix_ports=leaf['ports'],source_ids=leaf['source_ids'],head_ids=leaf['head_ids'],
                              retired_source_ids=sorted(source_ids-set(intended)),matrix_groups=leaf['matrix_groups'])
                if free:
                    result['retired_field_ids']=sorted(retired_field)
                    result['source_mode']='native_pads';result['free_feeders']['accepted']=True
            except BaseException:
                for item in list(board.GetTracks()):
                    uid=item.m_Uuid.AsString()
                    if uid not in commit_retained['tracks']:board.Delete(item)
                    elif item.GetNetname()!=commit_retained['tracks'][uid]['net']:item.SetNet(board.FindNet(commit_retained['tracks'][uid]['net']))
                native_pads={d.m_Uuid.AsString():d for f in board.GetFootprints() for d in f.Pads()}
                for f in entry['footprints'].values():
                    for q in f['pads']:native_pads[q['uuid']].SetNet(board.FindNet(q['net']))
                for clone in removed_copies.values():board.Add(clone)
                board.BuildConnectivity()
                if snapshot(board)!=entry or FrozenProofRouter._physical_state(SimpleNamespace(board=board))!=physical_seal:
                    raise ValueError('Conditional matrix commit rollback failed')
                if _source_physics(board,set(ownership.tracks)|set(ownership.pads))!=ownership.physical_sha256:
                    raise ValueError('Conditional matrix rollback changed raw source properties')
                raise
        elif snapshot(board)!=entry:raise ValueError('Rejected conditional matrix search changed its caller')
    result['elapsed_s']=time.monotonic()-started
    return result
