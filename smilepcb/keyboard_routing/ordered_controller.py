"""Prepare native ordered banks and their shared GPIO model before compose."""
from copy import deepcopy
import time
from collections import defaultdict
from smilepcb.native_worker import snapshot
from smilepcb.keyboard_phases import ordered_nets
from .gpio import apply_matrix_gpio,apply_exit_assignment
from .controller_ports import route_controller_ports
from .controller_bundles import extend_controller_bundles
from .power import seed_controller_returns
from .interface_contract import capture_interfaces,mcu_signal_nets


def probe_gpio_access(board,cfg,*,deadline,exit_bounds_nm=None):
    from smilepcb.keyboard_layout import ROW_PINS,COL_PINS
    from .local_router import LocalRouter,vec
    from .bank_geometry import escape_path,port_outside_bounds
    from smilepcb.native_worker import p
    chip=next(f for f in board.GetFootprints() if f.GetReference()=='U_MCU');part=next(q for q in cfg['parts'] if q['ref']=='U_MCU');pads={d.GetNumber():d for d in chip.Pads() if d.GetNumber()};safe=set(ROW_PINS)|set(COL_PINS);phases=dict(ordered_nets(cfg));names=set(phases['rows']+phases['columns']);excluded=[];checked=0;certified=[];uncertain=[];witnesses={}
    oracle=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000);center=chip.GetPosition()
    for gpio,number in sorted(cfg['mcu_gpio_pads'].items()):
        net=part['nets'][number]
        if gpio not in safe or not (net in names or net.startswith('NC_') or number in part.get('no_connect',()) and net.startswith('unconnected-')):continue
        if oracle.expired():raise TimeoutError('GPIO access preflight deadline exhausted')
        pad=pads[number];origin=(pad.GetPosition().x,pad.GetPosition().y);delta=(origin[0]-center.x,origin[1]-center.y);axis=0 if abs(delta[0])>abs(delta[1]) else 1;sign=1 if delta[axis]>0 else -1;face=chip.GetLayer();bb=pad.GetEffectiveShape(face).BBox();edge=(bb.GetRight() if axis==0 else bb.GetBottom()) if sign>0 else (bb.GetLeft() if axis==0 else bb.GetTop());neck=abs(edge-origin[axis])+max(oracle.minimum_clearance,173000)+oracle.track_width//2+1000;available=False;certified_path=None
        for length in (1000000,1400000,2000000,2600000,3400000,4200000):
            for shift in (0,-500000,500000,-1000000,1000000):
                path=escape_path(origin,axis,sign,origin[axis]+sign*length,shift,neck);checked+=1
                if path and all(oracle.clear(p.SHAPE_SEGMENT(vec(a),vec(z),oracle.track_width),face,net) for a,z in zip(path,path[1:])):
                    available=True;heading=(sign,0) if axis==0 else (0,sign)
                    if exit_bounds_nm is None or port_outside_bounds(path[-1],heading,exit_bounds_nm,oracle.track_width):
                        certified_path=path;break
            if certified_path is not None:break
        if not available:excluded.append(gpio)
        elif certified_path is None:uncertain.append(gpio)
        else:certified.append(gpio);witnesses[gpio]={'path_nm':certified_path,'layer':face}
    return {'excluded_gpios':excluded,'certified_gpios':certified,'uncertain_gpios':uncertain,
            'witnesses':witnesses,'exit_bounds_nm':exit_bounds_nm,'checked_proposals':checked,
            'scope':'individual native same-face witnesses; exclusions are advisory misses, not electrical unavailability; joint capacity is unproven; all misses retain native fallback'}


def prepare_ordered_controller(board,cfg,*,deadline,complete_matrix=False,on_rejection=None,exit_bounds_nm=None,prepared_usb=None,recovery_deadline=None,coherent_via_banks=False,unified_controller_plan=False,component_corridors=False,usb_max_length_nm=...,module_power_ids=None):
    from smilepcb.usb_policy import resolve_usb_max_length_nm
    usb_max_length_nm=resolve_usb_max_length_nm(usb_max_length_nm)
    from contextlib import nullcontext
    if type(component_corridors) is not bool:raise ValueError('Component corridors must be boolean')
    if component_corridors and not (complete_matrix and unified_controller_plan):raise ValueError('Component corridors requires complete matrix and unified controller planning')
    if type(coherent_via_banks) is not bool:raise ValueError('Coherent via banks must be boolean')
    if type(unified_controller_plan) is not bool:raise ValueError('Unified controller plan must be boolean')
    if unified_controller_plan and not complete_matrix:raise ValueError('Unified controller plan requires complete matrix preparation')
    source_options={'coherent_via_banks':True} if coherent_via_banks or unified_controller_plan else {}
    preplanned_source=None
    # Both cutoffs originate at placement entry; early phases retain deadline.
    final_deadline=max(deadline,recovery_deadline or deadline) if complete_matrix else deadline
    provisional_ownership=None
    early_local_ownership=None
    copper_copies=None
    if module_power_ids is not None:
        if not component_corridors:raise ValueError('Compiled supply recovery requires the complete corridor recipe')
        from .conditional_geometry import copy_native_tracks
        copper_copies=copy_native_tracks(board)
    before=snapshot(board);old_pads={d.m_Uuid.AsString():d.GetNetname() for f in board.GetFootprints() for d in f.Pads() if d.GetNumber()};result={'accepted':False}
    try:
        if deadline-time.monotonic()<2:raise TimeoutError('Ordered controller preparation deadline exhausted')
        if any(q['ref']=='J_UDB' for q in cfg['parts']):
            from .usb import route_controller_usb
            if prepared_usb is None:
                result['usb']=route_controller_usb(board,deadline=min(deadline-3,time.monotonic()+20),max_length_nm=usb_max_length_nm)
            else:
                from smilepcb.stm32.usb_placement import verify_prepared_usb
                result['usb']=verify_prepared_usb(board,prepared_usb,deadline=deadline-3,max_length_nm=usb_max_length_nm)
            if not result['usb']['accepted']:raise ValueError('USB preparation rejected: '+result['usb'].get('reason','unknown'))
        if complete_matrix and not component_corridors:
            from .modules import seed_key_data_access
            from .collector_preparation import verified_provisional_ownership,ProvisionalOwnership
            access_before=snapshot(board)
            result['key_data_access']=seed_key_data_access(board,cfg,deadline=deadline-8)
            if not result['key_data_access']['accepted']:
                raise ValueError('LED data access preparation rejected: '+result['key_data_access'].get('reason','Incomplete access'))
            provisional_ownership=verified_provisional_ownership(board,cfg,access_before,result['key_data_access'],deadline=deadline-8)
        from .generation import current_generation_profile
        generation=current_generation_profile()
        if unified_controller_plan and generation.name!='dsn-process-v2':raise ValueError('Unified controller plan requires explicit v2 generation')
        phases=dict(ordered_nets(cfg));names=set(phases['rows']+phases['columns'])
        refs={q['ref'] for q in cfg['parts'] if q['region']=='controller' and not q['ref'].startswith('#')}
        def reserve_returns():
            result['returns']=seed_controller_returns(board,controller_refs=refs if complete_matrix else {'U_MCU'},plane_nets=set(cfg['plane_nets']),deadline=min(deadline-3,time.monotonic()+15),via_to_smd_clearance_nm=200000)
            result['returns']['scope']='all controller return islands' if complete_matrix else 'MCU-connected local power groups; independent auxiliary returns are reserved later'
            if not result['returns']['accepted']:raise ValueError('Controller return reservation rejected')
        # Keep return access as real obstacles before local supply/signal joins.
        early_returns=generation.name=='dsn-process-v2' and complete_matrix
        if early_returns:reserve_returns()
        local_closed=None
        if generation.name=='dsn-process-v2':
            if module_power_ids is not None:
                from .controller_recovery import recover_controller_locals
                result['controller_local']=recover_controller_locals(board,cfg,module_power_ids=module_power_ids,
                    deadline=deadline,usb_max_length_nm=usb_max_length_nm)
                if not result['controller_local']['accepted']:
                    raise ValueError('Controller-local recovery rejected: '+result['controller_local'].get('reason','unknown'))
                local_closed=result['controller_local']['local_closed']
                early_local_ownership=getattr(result['controller_local'],'controller_ownership',None)
            elif component_corridors:
                from .controller_local import capture_local_closures
                local_closed=capture_local_closures(board,cfg,deadline=deadline)
                result['controller_local']=dict(accepted=True,read_only=True,local_closed=local_closed)
            else:
                from .controller_local import prepare_controller_local
                result['controller_local']=prepare_controller_local(board,cfg,deadline=deadline)
                local_closed=result['controller_local'].get('local_closed')
        if not early_returns:reserve_returns()
        if unified_controller_plan:
            result['fixed_interfaces']={'accepted':True,'ports':[]}
        else:
            fixed_before=snapshot(board) if complete_matrix else None
            result['fixed_interfaces']=route_controller_ports(board,controller_refs=refs,mcu_refs={'U_MCU'},plane_nets=cfg['plane_nets'],usb_nets={'USB_DM','USB_DP'},include_nets=mcu_signal_nets(cfg)-names-set(local_closed['nets'] if local_closed else ()),access_mode='prefer_same_layer',coherent_banks=False,continuation_nm=500000,deadline=min(deadline-3,time.monotonic()+15),clearance_nm=max(board.GetDesignSettings().m_MinClearance,173000))
            if not result['fixed_interfaces']['accepted']:raise ValueError('Fixed controller function reservation rejected: '+result['fixed_interfaces'].get('reason','unknown'))
            if local_closed is not None:result['fixed_interfaces']['local_closed']=local_closed
            if complete_matrix:
                fixed_ownership=verified_provisional_ownership(board,cfg,fixed_before,result['fixed_interfaces'],deadline=deadline-3)
                provisional_ownership=ProvisionalOwnership(provisional_ownership.generation_profile,{**provisional_ownership.tracks,**fixed_ownership.tracks},{**provisional_ownership.pads,**fixed_ownership.pads})
        from .interface_launches import reserve_launches
        with reserve_launches(result['fixed_interfaces']['ports']):
            result['access_preflight']=probe_gpio_access(board,cfg,deadline=deadline-2,exit_bounds_nm=exit_bounds_nm)
        updated,result['allocation']=apply_matrix_gpio(board,cfg,banked=True,
            # Individual same-face templates do not establish electrical
            # unavailability. Keep their misses mandatory and deprioritize
            # them together with the other unproved physical GPIOs. Retain
            # missed-prefix confidence separately as the secondary tie-break.
            excluded_gpios=(),
            uncertain_gpios=result['access_preflight']['uncertain_gpios']+result['access_preflight']['excluded_gpios'],
            unwitnessed_gpios=result['access_preflight']['excluded_gpios'])
        targets=defaultdict(list)
        for f in board.GetFootprints():
            if f.GetReference()=='U_MCU':continue
            for d in f.Pads():
                if d.GetNumber() and d.GetNetname() in names:targets[d.GetNetname()].append((d.GetPosition().x,d.GetPosition().y))
        targets={n:tuple(sum(q[i] for q in rows)/len(rows) for i in (0,1)) for n,rows in targets.items()}
        width=board.GetDesignSettings().m_TrackMinWidth or 150000;clearance=max(board.GetDesignSettings().m_MinClearance,173000)
        minimum_pitch=((width+clearance+20000+49999)//50000)*50000
        if complete_matrix:
            modules_first=generation.name=='dsn-process-v2' and not component_corridors
            if modules_first:
                # Local joins constrain the source outlets. Keep their real
                # copper in every later source snapshot and pitch rollback.
                from .modules import connect_key_modules
                with reserve_launches(result['fixed_interfaces']['ports']):
                    result['local_modules']=connect_key_modules(board,updated,deadline=deadline-20,via_to_smd_clearance_nm=200000)
                if not result['local_modules']['accepted']:
                    raise ValueError('Local key prerequisite preparation rejected: '+result['local_modules'].get('reason','Incomplete local modules'))
            if unified_controller_plan:
                from .controller_plan import prepare_unified_sources
                result['unified_controller_plan']={'accepted':False}
                preplanned_source,result['fixed_interfaces'],result['unified_controller_plan']=prepare_unified_sources(
                    board,updated,controller_refs=refs,matrix_nets=names,local_closed=local_closed,deadline=deadline,
                    exit_bounds_nm=exit_bounds_nm,coherent_via_banks=True,receipt=result['unified_controller_plan'],**({'component_corridors':True} if component_corridors else {}),**({'corridor_valid_until':final_deadline} if module_power_ids is not None else {}))
                local_closed=result['fixed_interfaces']['local_closed']
                if early_local_ownership is not None:
                    from .collector_preparation import merge_provisional_ownership
                    preplanned_source.controller_ownership=merge_provisional_ownership(snapshot(board),names,
                        early_local_ownership,preplanned_source.controller_ownership)
                if component_corridors:updated=deepcopy(preplanned_source.batch.model)
            from .collector_preparation import prepare_collector_matrix
            corridor_plan=getattr(preplanned_source,'corridor_plan',None)
            matrix_deadline=final_deadline if module_power_ids is not None else deadline
            with corridor_plan.reserve(board,updated,deadline=matrix_deadline) if corridor_plan is not None else nullcontext():
                if component_corridors:
                    from .modules import seed_key_data_access
                    from .collector_preparation import verified_provisional_ownership
                    access_before=snapshot(board)
                    result['key_data_access']=seed_key_data_access(board,updated,deadline=matrix_deadline-8)
                    if not result['key_data_access']['accepted']:
                        raise ValueError('LED data access preparation rejected: '+result['key_data_access'].get('reason','Incomplete access'))
                    provisional_ownership=verified_provisional_ownership(board,updated,access_before,result['key_data_access'],deadline=matrix_deadline-8)
                updated,prepared=prepare_collector_matrix(board,updated,controller_refs=refs,nets=names,targets=targets,fixed_ports=result['fixed_interfaces']['ports'],provisional_ownership=provisional_ownership,deadline=matrix_deadline-8,recovery_deadline=final_deadline-8,on_rejection=on_rejection,exit_bounds_nm=exit_bounds_nm,require_existing_modules=modules_first,local_closed=local_closed,**source_options,**({'preplanned_source':preplanned_source} if preplanned_source is not None else {}))
            result.update(prepared)
            if not prepared['candidate_accepted']:raise ValueError('Full collector matrix rejected: '+prepared.get('reason','Candidate budget exhausted'))
            if not isinstance(prepared.get('interface'),dict) or prepared['interface'].get('schema')!=1:
                raise ValueError('Missing original collector interface receipt')
            deadline=final_deadline  # Successful matrix proof/receipts share its declared cutoff.
        else:
            result['trials']=[];original_tracks={t.m_Uuid.AsString() for t in board.GetTracks()}
            from .interface_launches import reserve_launches
            with reserve_launches(result['fixed_interfaces']['ports']):
                for pitch in (None,minimum_pitch,minimum_pitch+50000):
                    if deadline-time.monotonic()<3:break
                    result['escapes']=route_controller_ports(board,controller_refs=refs,mcu_refs={'U_MCU'},plane_nets=updated['plane_nets'],usb_nets={'USB_DM','USB_DP'},include_nets=names,access_mode='prefer_same_layer',continuation_nm=500000,deadline=min(deadline-2,time.monotonic()+35),clearance_nm=clearance,bank_pitch_nm=pitch,exit_bounds_nm={'U_MCU':exit_bounds_nm} if exit_bounds_nm is not None else None,**source_options)
                    result['bundles']=extend_controller_bundles(board,result['escapes']['ports'],deadline=deadline-1,targets=targets) if result['escapes']['accepted'] else {'accepted':False,'reason':'No accepted escapes'}
                    okay=result['escapes']['accepted'] and result['bundles']['accepted']
                    result['trials'].append({'bank_pitch_nm':pitch,'accepted':okay,'escapes':result['escapes'],'bundles':result['bundles']})
                    if okay:break
                    for t in list(board.GetTracks()):
                        if t.m_Uuid.AsString() not in original_tracks:board.Delete(t)
                else:okay=False
                if not result['trials'] or not result['trials'][-1]['accepted']:raise ValueError('Ordered escape/corridor candidates rejected within the shared budget')
            updated,result['assignment']=apply_exit_assignment(board,updated,result['bundles']['ports'],deadline=deadline)
            updated['routing_preparation']={'mode':'ordered-banks-v2'}
            if coherent_via_banks:updated['routing_preparation']['coherent_via_banks']=True
        from .generation import current_generation_profile
        updated['routing_preparation']['generation_profile']=current_generation_profile().name
        if complete_matrix:
            from .field_contract import capture,verify_native
            result['matrix_contract']=capture(snapshot(board),result['matrix'],prerequisite_ids=result['prerequisite_ids'])
            phases=dict(ordered_nets(updated));matrix_nets=set(phases['rows']+phases['columns'])
            if (result['matrix_contract'].get('scope')!='matrix-with-controller' or
                    {net for _,net in result['matrix_contract']['terminals'].values()}!=matrix_nets):
                raise ValueError('Full matrix handoff omitted required matrix terminal nets')
            verify_native(board,result['matrix_contract'],deadline=deadline)
        else:
            result['interface']=capture_interfaces(board,updated,result['assignment']['ports']+result['fixed_interfaces']['ports'],deadline=deadline,**({'local_closed':local_closed} if local_closed is not None else {}))
        result['accepted']=True
        return updated,result
    except (ValueError,TimeoutError) as error:
        result['reason']=str(error)
        return cfg,result
    finally:
        if not result['accepted']:
            result.pop('interface',None)
            result.pop('matrix_contract',None)
            if copper_copies is not None:
                from .controller_recovery import restore_tracks
                restore_tracks(board,before,copper_copies)
            else:
                for t in list(board.GetTracks()):
                    uid=t.m_Uuid.AsString()
                    if uid not in before['tracks']:board.Delete(t)
                    elif t.GetNetname()!=before['tracks'][uid]['net']:t.SetNet(board.FindNet(before['tracks'][uid]['net']))
            for f in board.GetFootprints():
                for d in f.Pads():
                    if d.GetNumber():d.SetNet(board.FindNet(old_pads[d.m_Uuid.AsString()]))
            board.BuildConnectivity()
            if snapshot(board)!=before:raise ValueError('Ordered preparation rollback changed source')
