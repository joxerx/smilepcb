"""Backtrack physical source choices against complete matrix admission."""
import time,traceback
from pathlib import Path
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from smilepcb.native_worker import p,snapshot
from .controller_ports import route_controller_ports
from .gpio import apply_exit_assignment
from .interface_launches import reserve_launches
from . import matrix_stage
from .collector_assignment import collector_assignment
from smilepcb.keyboard_phases import ordered_nets



@dataclass(frozen=True)
class ProvisionalOwnership:
    generation_profile: str
    tracks: dict
    pads: dict


def verified_provisional_ownership(board,cfg,before,proof,*,deadline):
    """Bind actual current-function access output, never a request ID list."""
    from .generation import current_generation_profile
    from .native_graph import copper_graph
    from .interface_contract import mcu_signal_nets
    if not isinstance(proof,dict) or proof.get('accepted') is not True:
        raise ValueError('Access ownership requires an accepted native proof')
    after=snapshot(board);delta=set(after['tracks'])-set(before['tracks'])
    added=proof.get('added_ids',[]);ports=proof.get('ports',[])
    if (not isinstance(added,list) or len(added)!=len(set(added)) or set(added)!=delta or
            before['footprints']!=after['footprints'] or any(after['tracks'].get(u)!=v for u,v in before['tracks'].items())):
        raise ValueError('Access ownership differs from fresh native generation')
    models={part['ref']:part for part in cfg['parts']}
    pads={d['uuid']:(f['ref'],d) for f in after['footprints'].values() for d in f['pads'] if d['number']}
    data_refs={'LED'+str(key['id']) for key in cfg['layout']['keys']} if cfg['layout']['rgb'] else set()
    expected_data=Counter((ref,d['number'],d['net']) for ref,d in pads.values()
        if ref in data_refs and d['number'] in ('2','3') and d['net'] and not d['net'].startswith(('NC_','unconnected-')))
    key_ports='required' in proof
    if key_ports and (proof.get('required')!=sum(expected_data.values()) or
            Counter((q.get('ref'),q.get('pin'),q.get('net')) for q in ports)!=expected_data):
        raise ValueError('Key access proof differs from actual data pads')
    matrix=dict(ordered_nets(cfg)) if not key_ports else {'rows':[],'columns':[]}
    matrix_nets=set(matrix['rows']+matrix['columns'])
    required={uid for uid,(ref,d) in pads.items() if ref=='U_MCU' and d['net'] in mcu_signal_nets(cfg)-matrix_nets} if not key_ports else set()
    graph=None
    if not key_ports and proof.get('local_closed') is not None:
        from .controller_local import verify_local_closures
        graph=copper_graph(board,{q['net'] for q in ports}|set(proof['local_closed']['nets']),deadline,include_zones=False)
        covered=verify_local_closures(board,cfg,proof['local_closed'],deadline=deadline,graph=graph)
        required-=set().union(*covered.values()) if covered else set()
    groups=[];claimed=set();seen=set()
    for q in ports:
        net=q['net'];owned=q.get('ids',[]) if key_ports else q.get('added_ids',[])
        source=[u for u,(ref,d) in pads.items() if (ref,d['number'],d['net'])==(q['ref'],q['pin'],net)] if key_ports else q.get('pad_ids',[])
        if not source or any(u not in pads or models.get(pads[u][0],{}).get('nets',{}).get(pads[u][1]['number'])!=net or pads[u][1]['net']!=net for u in source):
            raise ValueError('Access source differs from the physical model binding')
        if not key_ports and (not any(u in required for u in source) or any(models[pads[u][0]]['region']!='controller' for u in source)):
            raise ValueError('Fixed access does not own a required MCU function')
        seen.update(set(source)&required);claimed.update(owned)
        if any(u not in delta or after['tracks'][u]['net']!=net or after['tracks'][u]['class'] not in ('PCB_TRACK','PCB_VIA') for u in owned):
            raise ValueError('Access proof claimed old or unrelated source copper')
        via=q.get('via_id')
        if key_ports and q.get('connected') is not True:raise ValueError('Key access has no accepted layer contact')
        if via is not None:
            shape=after['tracks'].get(via,{})
            if (shape.get('class')!='PCB_VIA' or shape.get('net')!=net or shape.get('via_type')!=p.VIATYPE_THROUGH or
                    {shape.get('top'),shape.get('bottom')}!={p.F_Cu,p.B_Cu} or not 0<shape.get('drill',0)<shape.get('width',0)):
                raise ValueError('Access via differs from native geometry')
        if key_ports and via is None:raise ValueError('Key access lost its physical via')
        if not key_ports:
            tip=q.get('terminal_id') or via;shape=after['tracks'].get(tip,{})
            exit_nm=tuple(q.get('exit_nm',()));origin=tuple(q.get('heading_origin_nm',()))
            if tip not in owned or shape.get('net')!=net:raise ValueError('Fixed access lost its owned head')
            if shape.get('class')=='PCB_TRACK':
                ends=[tuple(shape['start']),tuple(shape['end'])]
                if shape['layer']!=q['exit_layer'] or exit_nm not in ends or origin!=ends[1-ends.index(exit_nm)] or exit_nm==origin:
                    raise ValueError('Fixed access head geometry differs')
            elif shape.get('class')=='PCB_VIA':
                if exit_nm!=tuple(shape['start']) or not any(after['tracks'][u]['class']=='PCB_TRACK' and
                        after['tracks'][u]['layer']==q['exit_layer'] and {tuple(after['tracks'][u]['start']),tuple(after['tracks'][u]['end'])}=={origin,exit_nm} for u in owned):
                    raise ValueError('Fixed access via has no physical heading')
            else:raise ValueError('Fixed access has an invalid terminal')
            if (q.get('kind')=='same_layer' and via is not None or q.get('kind')=='via' and
                    (via not in owned or tuple(q.get('via_nm',()))!=tuple(after['tracks'][via]['start'])) or q.get('kind') not in ('same_layer','via')):
                raise ValueError('Fixed access via metadata differs')
        groups.append((source,owned,via))
    if claimed!=delta or seen!=required:raise ValueError('Access proof omitted actual generated ownership or source pads')
    if groups:
        # One native graph for the whole accepted batch, not one per pad/head.
        if graph is None:graph=copper_graph(board,{q['net'] for q in ports},deadline)
        ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])}
        for source,owned,via in groups:
            members=set(source)|set(owned)|({via} if via is not None else set())
            if not members<=ids.keys() or len({graph['find'](ids[u]) for u in members})!=1:
                raise ValueError('Access ownership has no actual native pad contact')
    if time.monotonic()>=deadline:raise TimeoutError('Access ownership proof deadline exhausted')
    return ProvisionalOwnership(current_generation_profile().name,
        {u:deepcopy(after['tracks'][u]) for u in delta if not after['tracks'][u]['locked']},
        {u:deepcopy(pads[u][1]) for source,_,_ in groups for u in source})



def _verified_participant_delta(board,cfg,before,proof,*,required,allowed_nets,deadline):
    """Own an internal snapshot -> actual creator -> checker delta only.

    Caller chronology is trusted; request/serialized before/proof dictionaries
    are not authority to issue this in-process ownership.
    """
    from .generation import current_generation_profile
    from .native_graph import copper_graph
    if time.monotonic()>=deadline:raise TimeoutError('Controller ownership proof deadline exhausted')
    if not isinstance(proof,dict) or proof.get('accepted') is not True:
        raise ValueError('Controller ownership requires an accepted creator')
    after=snapshot(board);added=proof.get('added_ids');delta=set(after['tracks'])-set(before['tracks'])
    if (not isinstance(added,list) or len(added)!=len(set(added)) or set(added)!=delta or
            after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=q for u,q in before['tracks'].items())):
        raise ValueError('Controller ownership differs from fresh native creation')
    models={q['ref']:q for q in cfg['parts']}
    pads={d['uuid']:(f['ref'],d) for f in after['footprints'].values() for d in f['pads'] if d['number']}
    if not set(required)<=pads.keys():raise ValueError('Controller creator omitted an actual pad')
    for uid in required:
        ref,d=pads[uid];model=models.get(ref,{})
        if model.get('region')!='controller' or model.get('nets',{}).get(d['number'])!=d['net'] or d['net'] not in allowed_nets:
            raise ValueError('Controller ownership changed model or physical pad binding')
    if any(after['tracks'][u]['net'] not in allowed_nets or after['tracks'][u]['class'] not in ('PCB_TRACK','PCB_VIA') for u in delta):
        raise ValueError('Controller creator claimed unrelated copper')
    if required or delta:
        graph=copper_graph(board,set(allowed_nets),deadline,include_zones=False)
        ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])}
        roots={}
        for uid in required:
            if uid not in ids:raise ValueError('Controller ownership lost an actual pad')
            roots.setdefault(pads[uid][1]['net'],set()).add(graph['find'](ids[uid]))
        if any(len(v)!=1 for v in roots.values()):raise ValueError('Controller creator left a required pad group open')
        if any(u not in ids or graph['find'](ids[u]) not in roots.get(after['tracks'][u]['net'],set()) for u in delta):
            raise ValueError('Controller creator added copper without a controller pad contact')
    if time.monotonic()>=deadline:raise TimeoutError('Controller ownership proof deadline exhausted')
    return ProvisionalOwnership(current_generation_profile().name,
        {u:deepcopy(after['tracks'][u]) for u in delta if not after['tracks'][u]['locked']},
        {u:deepcopy(pads[u][1]) for u in required})


def verified_return_tree_ownership(board,cfg,before,proof,*,deadline):
    """Validate the return creator's controller-only required-pad partition."""
    if not isinstance(proof,dict) or proof.get('source_copper_unchanged') is not True or proof.get('missing_checked') is not True or proof.get('missing')!={}:
        raise ValueError('Controller return ownership requires a complete native proof')
    state=snapshot(board);refs={q['ref'] for q in cfg['parts'] if q['region']=='controller' and not q['ref'].startswith('#')};planes=set(cfg['plane_nets'])
    required={d['uuid'] for f in state['footprints'].values() if f['ref'] in refs for d in f['pads'] if d['number'] and d['net'] in planes}
    supplied=proof.get('required_pad_ids')
    if (not isinstance(supplied,list) or len(supplied)!=len(set(supplied)) or set(supplied)!=required or
            type(proof.get('required')) is not int or proof['required']!=len(required)):
        raise ValueError('Controller return proof differs from actual required pad aliases')
    return _verified_participant_delta(board,cfg,before,proof,required=required,allowed_nets=planes,deadline=deadline)


def verified_controller_local_ownership(board,cfg,before,proof,*,deadline):
    """Local obligations own newly routed bodies, without fictitious ports."""
    from .controller_plan import verify_controller_local_subsets
    if not isinstance(proof,dict) or proof.get('accepted') is not True:raise ValueError('Missing accepted local controller creator')
    subsets=verify_controller_local_subsets(board,cfg,proof,deadline=deadline)
    required={u for row in subsets.values() for u in row['pads']}
    return _verified_participant_delta(board,cfg,before,proof,required=required,allowed_nets=set(subsets),deadline=deadline)


def merge_provisional_ownership(state,nets,*owners):
    """Rebind each real creator at the current frame before combining it."""
    from .generation import current_generation_profile
    tracks={};pads={};present=False
    for owner in owners:
        if owner is None:continue
        transient_access_ids(state,owner,nets);present=True
        for target,incoming in ((tracks,owner.tracks),(pads,owner.pads)):
            if any(u in target and target[u]!=q for u,q in incoming.items()):raise ValueError('Conflicting controller creator ownership')
            target.update(deepcopy(incoming))
    return ProvisionalOwnership(current_generation_profile().name,tracks,pads) if present else None



def contract_provisional_ownership(state,ownership,nets):
    """Keep only unchanged old witnesses; never adopt repair descendants."""
    from .generation import current_generation_profile
    if (not isinstance(ownership,ProvisionalOwnership) or
            ownership.generation_profile!=current_generation_profile().name or
            any(q['locked'] or q['net'] in nets for q in ownership.tracks.values())):
        raise ValueError('Cannot contract invalid participant ownership')
    narrowed=ProvisionalOwnership(ownership.generation_profile,
        {u:deepcopy(q) for u,q in ownership.tracks.items() if state['tracks'].get(u)==q},
        deepcopy(ownership.pads))
    transient_access_ids(state,narrowed,nets)
    return narrowed


def transient_access_ids(state,ownership,nets):
    from .generation import current_generation_profile
    if ownership is None:return set()
    if not isinstance(ownership,ProvisionalOwnership) or ownership.generation_profile!=current_generation_profile().name:
        raise ValueError('Transient access requires current internal native ownership')
    tracks=state['tracks'];pads={d['uuid']:d for f in state['footprints'].values() for d in f['pads']}
    if any(pads.get(u)!=shape for u,shape in ownership.pads.items()):
        raise ValueError('Transient source pad shape or binding changed before matrix capture')
    if any(u not in tracks or tracks[u]!=shape or shape['locked'] or shape['net'] in nets for u,shape in ownership.tracks.items()):
        raise ValueError('Transient access ownership changed before matrix capture')
    return set(ownership.tracks)

def prepare_collector_matrix(board,cfg,*,controller_refs,nets,targets,fixed_ports,deadline,on_rejection=None,exit_bounds_nm=None,provisional_ownership=None,require_existing_modules=False,recovery_deadline=None,local_closed=None,coherent_via_banks=False,preplanned_source=None):
    from contextlib import nullcontext
    if type(coherent_via_banks) is not bool:raise ValueError('Coherent via banks must be boolean')
    corridor_plan=getattr(preplanned_source,'corridor_plan',None)
    collector_started=time.monotonic()
    before=snapshot(board);pad_nets={d.m_Uuid.AsString():d.GetNetname() for f in board.GetFootprints() for d in f.Pads() if d.GetNumber()}
    copies=None;entry_physics=None;inventory=None;launches=None
    if preplanned_source is not None and corridor_plan is None:
        from .conditional_banks import copy_native_tracks,_source_physics
        from .interface_launches import current_launch_inventory
        copies=copy_native_tracks(board)
        physics_ids=set(before['tracks'])|{d['uuid'] for f in before['footprints'].values() for d in f['pads']}
        entry_physics=_source_physics(board,physics_ids)
        inventory=current_launch_inventory()
        if inventory is not None:launches=(deepcopy(inventory.ports),set(inventory.resolved),set(inventory.repair_reserved))

    transient_access_ids(before,provisional_ownership,nets)
    result={'candidate_accepted':False,'trials':[]}
    width=board.GetDesignSettings().m_TrackMinWidth or 150000;gap=max(board.GetDesignSettings().m_MinClearance,173000)
    pitch=((width+gap+20000+49999)//50000)*50000
    pitches=(None,) if preplanned_source is not None else (None,pitch,pitch+50000)
    from .generation import current_generation_profile
    coupled=current_generation_profile().name=='dsn-process-v2'
    source_deadline=min(deadline-12,collector_started+90) if coupled else deadline-12
    fallback_attempted=False
    def rollback():
        if copies is not None:
            live=snapshot(board)['tracks']
            for t in list(board.GetTracks()):
                uid=t.m_Uuid.AsString()
                if uid not in before['tracks'] or live[uid]!=before['tracks'][uid]:board.Delete(t)
            present={t.m_Uuid.AsString() for t in board.GetTracks()}
            for uid,clone in copies.items():
                if uid not in present:board.Add(clone)
        else:
            for t in list(board.GetTracks()):
                uid=t.m_Uuid.AsString()
                if uid not in before['tracks']:board.Delete(t)
                elif t.GetNetname()!=before['tracks'][uid]['net']:t.SetNet(board.FindNet(before['tracks'][uid]['net']))
        for f in board.GetFootprints():
            for d in f.Pads():
                if d.GetNumber():d.SetNet(board.FindNet(pad_nets[d.m_Uuid.AsString()]))
        board.BuildConnectivity()
        if inventory is not None:inventory.ports,inventory.resolved,inventory.repair_reserved=deepcopy(launches)
        if snapshot(board)!=before:raise ValueError('Matrix candidate rollback changed retained source geometry')
        if copies is not None and _source_physics(board,physics_ids)!=entry_physics:
            raise ValueError('Matrix candidate rollback changed native source properties')
    try:
        for index,spacing in enumerate(pitches):
            if deadline-time.monotonic()<20:break
            # The coupled bank search owns remaining shared placement time.
            # Its source alternatives replace the old reserved pitch windows;
            # no new worker allowance is introduced.
            share=max(0,deadline-time.monotonic()) if coupled else max(30,deadline-time.monotonic()-30*(len(pitches)-index-1))
            stop=min(deadline,time.monotonic()+share)
            trial={'bank_pitch_nm':spacing,'accepted':False,'budget_allocation':'coupled_banks_shared_remaining_deadline' if coupled else 'legacy_pitch_reservation'};candidate_cfg=cfg;trial_started=time.monotonic()
            try:
                source_before=snapshot(board)
                source_stop=min(stop-12,source_deadline,time.monotonic()+35)
                if source_stop<=time.monotonic():raise TimeoutError('Controller source shared allowance exhausted')
                if preplanned_source is not None:
                    source=preplanned_source.take(board,cfg,nets,deadline=source_stop)
                else:
                    with reserve_launches(fixed_ports):
                        source=route_controller_ports(board,controller_refs=controller_refs,mcu_refs={'U_MCU'},plane_nets=cfg['plane_nets'],usb_nets={'USB_DM','USB_DP'},include_nets=nets,access_mode='prefer_same_layer',continuation_nm=500000,deadline=source_stop,clearance_nm=gap,bank_pitch_nm=spacing,coherent_via_banks=coherent_via_banks,exit_bounds_nm={'U_MCU':exit_bounds_nm} if exit_bounds_nm is not None else None)
                        if not source['accepted'] and coupled and not fallback_attempted and min(stop-12,source_deadline)-time.monotonic()>5:
                            from .controller_escape import route_controller_escapes
                            fallback_attempted=True;trial['stock_escapes']=source
                            source=route_controller_escapes(board,controller_refs=controller_refs,mcu_refs={'U_MCU'},include_nets=nets,
                                exit_bounds_nm={'U_MCU':exit_bounds_nm} if exit_bounds_nm is not None else None,
                                deadline=min(stop-12,source_deadline),clearance_nm=gap)
                            if coherent_via_banks:
                                from .source_policy import legacy_fallback_provenance
                                source=legacy_fallback_provenance(source,trial['stock_escapes'])
                trial['escapes']=source
                if not source['accepted']:raise ValueError('Joint source interface search rejected')
                phases=dict(ordered_nets(cfg));families={n:r for r in ('rows','columns') for n in phases[r]}
                if corridor_plan is not None:
                    from .controller_corridor_plan import collector_plan_assignment
                    mapping,plans=collector_plan_assignment(board,cfg,preplanned_source,source,deadline=stop-6)
                else:mapping,plans=collector_assignment(source['ports'],targets,families)
                candidate_cfg,assignment=apply_exit_assignment(board,cfg,source['ports'],deadline=stop-6,net_mapping=mapping)
                assignment.update(strategy='collector-relative-v1',collector_plans=plans)
                planned={n:plan['collector_heading'] for plan in plans for n in plan['signal_peel_order']}
                for port in assignment['ports']:port['collector_heading']=planned[port['net']]
                candidate_cfg['routing_preparation']={'mode':'ordered-banks-v2','generation_profile':current_generation_profile().name}
                if coherent_via_banks:candidate_cfg['routing_preparation']['coherent_via_banks']=True
                if preplanned_source is not None:candidate_cfg['routing_preparation']['unified_controller_plan']=True
                if corridor_plan is not None:candidate_cfg['routing_preparation']=deepcopy(cfg['routing_preparation'])
                generated_heads=None
                retained_source=preplanned_source is not None and corridor_plan is None
                if retained_source:
                    # Authenticate the immutable take frame before any module or
                    # return additions. These heads are retained additively.
                    trial['retained_source_ids']=sorted(preplanned_source.matrix_delta(board,candidate_cfg,assignment['ports'],deadline=stop-6))
                elif coupled:
                    from .conditional_banks import certify_generated_heads
                    generated_heads=certify_generated_heads(board,candidate_cfg,source_before,assignment['ports'],deadline=stop-6,**({'source_batch':preplanned_source} if preplanned_source is not None else {}))
                ports=assignment['ports']+fixed_ports
                chip=next(f for f in board.GetFootprints() if f.GetReference()=='U_MCU');row=chip.GetLayer();column=p.B_Cu if row==p.F_Cu else p.F_Cu
                with reserve_launches(ports), corridor_plan.reserve(board,candidate_cfg,deadline=stop) if corridor_plan is not None else nullcontext():
                    from .modules import connect_key_modules
                    from .power import connect_controller_return_tree
                    modules_before=snapshot(board) if require_existing_modules else None
                    local=connect_key_modules(board,candidate_cfg,deadline=stop-12,via_to_smd_clearance_nm=200000)
                    trial['local_modules']=local
                    if not local['accepted']:raise ValueError('Incomplete local key prerequisites')
                    if require_existing_modules and snapshot(board)!=modules_before:
                        raise ValueError('Post-source guard changed prebuilt local modules')
                    if coupled:
                        from .matrix_layers import select_board_matrix_layers
                        selected_layers=select_board_matrix_layers(board,candidate_cfg,families,
                            faces=(p.F_Cu,p.B_Cu),preferred_row_layer=row,deadline=stop-12)
                        row,column=selected_layers['row_layer'],selected_layers['column_layer']
                        trial['field_layer_selection']=selected_layers
                    returns_before=snapshot(board) if retained_source else None
                    returns=connect_controller_return_tree(board,candidate_cfg,deadline=stop-12)
                    trial['controller_return_tree']=returns
                    if not returns['accepted']:raise ValueError('Controller return tree preparation rejected: '+returns.get('reason','Incomplete returns'))
                    current=snapshot(board);transient=transient_access_ids(current,provisional_ownership,nets);participants=None
                    if retained_source:
                        returned=verified_return_tree_ownership(board,candidate_cfg,returns_before,returns,deadline=stop-6)
                        participants=merge_provisional_ownership(current,nets,getattr(preplanned_source,'controller_ownership',None),returned)
                        transient.update(transient_access_ids(current,participants,nets))
                    prerequisites=sorted(u for u,t in current['tracks'].items() if t['net'] not in nets and u not in transient)
                    # Capture the actual assigned heads before matrix routing can retire them.
                    from .interface_contract import capture_interfaces,verify_interfaces
                    interface=capture_interfaces(board,candidate_cfg,ports,deadline=stop-2,local_closed=local_closed)
                    matrix=matrix_stage.prepare_matrix(board,candidate_cfg,row_layer=row,column_layer=column,deadline=stop-2,recovery_deadline=max(deadline,recovery_deadline or deadline)-2,ports=ports,collectors=not retained_source,generated_heads=generated_heads,exit_bounds_nm=exit_bounds_nm,**({'participant_ownership':participants} if participants is not None else {}),**({'corridor_plan':corridor_plan} if corridor_plan is not None else {}))
                if corridor_plan is not None:
                    prerequisites=sorted(set(prerequisites)|set(matrix.get('component_corridors',{}).get('fixed_prerequisite_ids',())))
                trial['matrix']=matrix;trial['assignment']=assignment
                if not matrix['complete'] or not matrix.get('controller_connected'):raise ValueError('Full matrix rejected: '+matrix.get('reason','Missing physical terminal proof'))
                proof=verify_interfaces(board,candidate_cfg,interface,deadline=max(deadline,recovery_deadline or deadline),retire_completed=True)
                from .interface_launches import interface_identity
                resolved=set(map(tuple,proof['resolved']))
                if any(interface_identity(q) not in resolved for q in interface['ports'] if q['net'] in nets):
                    raise ValueError('Full matrix native closure is incomplete')
                assignment['ports']=deepcopy(matrix.get('matrix_ports',assignment['ports']))
                trial['accepted']=True;trial['elapsed_s']=time.monotonic()-trial_started;result['trials'].append(trial)
                result.update(candidate_accepted=True,escapes=source,assignment=assignment,matrix=matrix,prerequisite_ids=prerequisites,interface=interface);result.pop('reason',None)
                candidate_cfg['routing_preparation']['matrix']='collectors-v1'
                return candidate_cfg,result
            except (ValueError,TimeoutError) as error:
                trial['reason']=str(error);trial['elapsed_s']=time.monotonic()-trial_started
                trial['error_stack']=[{'module':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(error.__traceback__)]
                if on_rejection is not None:on_rejection(board,candidate_cfg,index,trial)
                result['trials'].append(trial);result.update(reason=str(error),matrix=trial.get('matrix',{}))
                rollback()
        return cfg,result
    finally:
        if not result['candidate_accepted']:rollback()
