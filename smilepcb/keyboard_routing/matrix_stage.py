"""Connect and clean a generated matrix before publishing its fixed receipt."""
import time
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_phases import ordered_nets
from .field import build_field
from .field_hygiene import remove_covered,clean_matrix_tracks
from .native_graph import route_components
from .matrix import KeyboardRouter
from .cleanup import cleanup_generated
from .completion import pad_partitions
from .handoff import preserves_contacts
from .feeder_repair import matrix_pad_state,repair_feeders


def _field_cutoff(start, deadline, recovery_deadline):
    # The key field and controller feeders share one matrix allowance.
    stop = max(deadline, recovery_deadline or deadline)
    return min(start + 65, stop - max(12, (stop - start) * .35))


def _prepare_matrix(board,cfg,*,row_layer,column_layer,deadline,ports=None,collectors=False,recovery_deadline=None,generated_heads=None,exit_bounds_nm=None,corridor_plan=None,participant_ownership=None):
    start=time.monotonic()
    if deadline-start<2:return {'complete':False,'controller_connected':False,'reason':'matrix budget exhausted'}
    before=snapshot(board);old_partitions=pad_partitions(board,min(deadline,start+8))
    phases=dict(ordered_nets(cfg));family={n:f for f in ('rows','columns') for n in phases[f]};nets=set(family)
    participant_ids=set()
    if participant_ownership is not None:
        from .collector_preparation import transient_access_ids
        participant_ids=transient_access_ids(before,participant_ownership,nets)
    controllers=[f for f in board.GetFootprints() if f.GetReference()=='U_MCU']
    if len(controllers)!=1:raise ValueError('A complete matrix needs exactly one MCU')
    mcu=controllers[0];part=next(q for q in cfg['parts'] if q['ref']=='U_MCU');controller_ids=[]
    for kind in ('rows','cols'):
        for pin in cfg['layout']['gpio'][kind]:
            number=cfg['mcu_gpio_pads'][pin];name=part['nets'][number]
            pads=[d for d in mcu.Pads() if d.GetNumber()==number]
            if not pads or any(d.GetNetname()!=name for d in pads):raise ValueError('MCU matrix pin binding differs from the model')
            controller_ids.extend(d.m_Uuid.AsString() for d in pads)
    report=build_field(board,cfg,row_layer=row_layer,column_layer=column_layer,
        deadline=_field_cutoff(start,deadline,recovery_deadline if generated_heads is not None else None))
    if not report['complete']:return dict(report,controller_connected=False)
    from copy import deepcopy
    from .interface_launches import interface_identity,current_launches,current_launch_inventory,publish_launches
    inherited={interface_identity(q):q for q in current_launches()}
    inherited.update({interface_identity(q):q for q in (ports or [])})
    source_ports=deepcopy(list(inherited.values()));live_ports=deepcopy(source_ports)
    conditional=None;source_protected=set(before['tracks'])-participant_ids
    source_protected.update(u for q in source_ports for u in q['added_ids'])
    participant_ids.difference_update(source_protected)
    if corridor_plan is not None:
        try:
            report['component_corridors']=corridor_plan.commit(board,cfg,deadline=deadline-12)
            # Preserve every committed body through fallback and cleanup;
            # matrix roles include only matrix copper, while fixed-function
            # joins remain ordinary protected prerequisites.
            items=snapshot(board)['tracks'];added=report['component_corridors']['added_ids']
            source_protected.update(added)
            report['component_corridors']['fixed_prerequisite_ids']=sorted(uid for uid in added if items[uid]['net'] not in nets)
            for uid in added:
                if items[uid]['net'] in nets:
                    report['owned_ids'].append(uid);report['generated_owned_ids'].append(uid)
                    report['roles'][uid]='row_access' if family[items[uid]['net']]=='rows' else 'column_access'
            pending=matrix_pad_state(board,nets,deadline)['missing']
            report['component_corridors']['pending_matrix']=pending
            if pending:
                return dict(report,complete=False,controller_connected=False,
                    reason='Planned controller corridors did not close the complete physical matrix',pending_matrix=pending)
            conditional=dict(accepted=True,supported=True,supplier='early_component_corridors')
            generated_heads=None  # Full Native closure consumed this replacement obligation.
        except (ValueError,TimeoutError) as error:
            return dict(report,complete=False,controller_connected=False,
                component_corridors=dict(accepted=False,reason=str(error)),reason=str(error))
    if generated_heads is not None:
        from .conditional_banks import prepare_conditional_banks
        repair_extra_seconds=max(0.,(recovery_deadline or deadline)-deadline)
        deadline=max(deadline,recovery_deadline or deadline)
        conditional=prepare_conditional_banks(board,cfg,report,ports,generated_heads,
            deadline=deadline-12,exit_bounds_nm=exit_bounds_nm,repair_extra_seconds=repair_extra_seconds)
        report['conditional_banks']=conditional
        report['budget_allocation']='remaining_shared_placement_deadline_minus_12s_cleanup'
        if conditional['supported'] and not conditional['accepted']:
            return dict(report,complete=False,controller_connected=False,reason='Conditional complete-bank search exhausted',
                pending_matrix=matrix_pad_state(board,nets,deadline)['missing'])
        if conditional['accepted']:
            retired=set(conditional.get('retired_field_ids',()))
            if retired:
                report['owned_ids']=[uid for uid in report['owned_ids'] if uid not in retired]
                report['generated_owned_ids']=[uid for uid in report['generated_owned_ids'] if uid not in retired]
                for uid in retired:del report['roles'][uid]
            replacement={interface_identity(q):q for q in conditional['matrix_ports']}
            source_ports=[deepcopy(replacement.get(interface_identity(q),q)) for q in source_ports]
            live_ports=deepcopy(source_ports)
            source_protected.difference_update(generated_heads.tracks)
            source_protected.update(conditional['source_ids'])
            source_protected.update(conditional['head_ids'])
    if collectors and ports and not (conditional and conditional['accepted']):
        from .collectors import route_collectors
        matrix_ports=[q for q in ports if q['net'] in nets]
        # Keep a portion of the SAME deadline for individual complete feeders,
        # cleanup and independent all-terminal proof. No new post-search budget.
        available=max(0,deadline-time.monotonic())
        report['collectors']=route_collectors(board,cfg,matrix_ports,field_owned_ids=report['owned_ids'],deadline=min(deadline-8,time.monotonic()+available*.45))
        updated={interface_identity(q):q for q in report['collectors'].get('ports',matrix_ports)}
        # Keep closed identities for transactional ripup; native graph proof
        # retires their guards while original metadata can reactivate them.
        live_ports=[updated.get(interface_identity(q),q) for q in source_ports]
    history=[];repair_history=[];feeder_ids=set()
    protected=source_protected|set(report['owned_ids'])
    from .interface_launches import reserve_launches
    with reserve_launches(live_ports or []):
        if conditional and conditional['accepted']:
            state=matrix_pad_state(board,nets,deadline);grids=()
        else:grids=(500000,125000)
        for resolution,step in enumerate(grids):
            for kind in ('rows','columns'):
                if deadline-time.monotonic()<12:break
                router=KeyboardRouter(board,deadline=min(deadline-10,time.monotonic()+25),compact=True,
                                      track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000)
                # The current scoped inventory includes any accepted repair;
                # never reinstall a pre-repair explicit terminal snapshot.
                if router.launch_policy is not None:router.port_policy=router.launch_policy
                router.grid_step=step;router.connection_budget_s=2 if step==500000 else 6;router.search_budget_s=router.connection_budget_s
                router.heuristic_weight=3;router.via_cost_nm=1000000;router.layer_change_heuristic_nm=0
                router.routing_penalty=(lambda shape,layer,net,via:0 if via or layer==row_layer else 2*shape.GetTotalLength()) if step==500000 else None
                tick=time.monotonic();count=route_components(board,router,set(phases[kind]),finer_grid_steps=grids[resolution+1:])
                if participant_ownership is not None:
                    live=snapshot(board)['tracks'];added=set(router.added)
                    if any(u not in live or live[u]['net'] not in nets or live[u]['locked'] for u in added):
                        raise ValueError('Matrix feeder creation lost its actual admitted ownership')
                    feeder_ids.update(added)
                history.append({'family':kind,'grid_nm':step,'connections':count,'elapsed_s':time.monotonic()-tick,
                                'graph':router.graph_diagnostic})
            state=matrix_pad_state(board,nets,deadline)
            if not state['missing']:break
            if step==500000:
                # Backtracking needs the same finite stage window as forward
                # search. Two fine-grid retries previously spent it all before
                # recovery could even start. Keep fine routing as the fallback.
                repair_protected=protected
                if participant_ownership is not None:
                    repair_protected|=set(snapshot(board)['tracks'])-feeder_ids-participant_ids
                repair_history=repair_feeders(board,nets,repair_protected,deadline=deadline-12,
                    ports=live_ports,source_ports=source_ports,**({'participant_ownership':participant_ownership} if participant_ownership is not None else {}))
                if repair_history:state=matrix_pad_state(board,nets,deadline)
                if not state['missing']:break
        # A bounded primary source trial must not discard an otherwise
        # complete matrix merely to spend its remaining time on worse sources.
        # Borrow once, only after an exact rollback and a time-limited search.
        missing=state['missing']
        last=repair_history[-1] if repair_history else {}
        timed_out=any(row.get('search',{}).get('reason')=='search_budget'
            for row in last.get('plan',{}).get('diagnostic',{}).get('attempts',[]))
        if (recovery_deadline is not None and recovery_deadline>deadline
                and recovery_deadline-time.monotonic()>20 and sum(missing.values())==1
                and last.get('accepted') is False and last.get('retained_pending')==missing and timed_out
                # The caller's initial witness is stale after any accepted
                # participant epoch. That engine already used its live owner.
                and not (participant_ownership is not None and any(q['accepted'] for q in repair_history))):
            report['borrowed_source_budget_s']=recovery_deadline-deadline
            deadline=recovery_deadline
            repair_protected=protected|(set(snapshot(board)['tracks'])-feeder_ids-participant_ids) if participant_ownership is not None else protected
            repair_history+=repair_feeders(board,nets,repair_protected,deadline=deadline-12,ports=live_ports,source_ports=source_ports,**({'participant_ownership':participant_ownership} if participant_ownership is not None else {}))
            state=matrix_pad_state(board,nets,deadline)
        report['feeder_repair_history']=repair_history
        live_ports=deepcopy(list(current_launches()))
        resolved=set(current_launch_inventory().resolved)
    required={d.m_Uuid.AsString():[family[d.GetNetname()],d.GetNetname()] for f in board.GetFootprints() for d in f.Pads() if d.GetNumber() and d.GetNetname() in nets}
    if state['pad_ids']!=set(required) or not set(controller_ids)<=state['pad_ids']:
        raise ValueError('Native matrix proof omitted a physical terminal')
    missing=state['missing']
    if not missing:
        original={interface_identity(q):q for q in source_ports}
        live_ports=[deepcopy(original[interface_identity(q)]) if q['net'] in nets else q for q in live_ports]
        resolved.update(interface_identity(q) for q in live_ports if q['net'] in nets)
    # A caller may continue routing after this nested matrix phase. Publish
    # only current physical interfaces, including originals used after ripup.
    publish_launches(live_ports,resolved=resolved)
    report.update(feeder_history=history,matrix_groups=state['groups'],controller_pad_ids=sorted(controller_ids))
    if missing or deadline-time.monotonic()<8:
        return dict(report,complete=False,controller_connected=False,pending_matrix=missing,reason='MCU matrix routing incomplete within the declared budget')
    from .matrix_polish import polish_matrix,prune_matrix_contacts
    stable=source_protected|set(report['owned_ids'])
    # All matrix terminals have native proof now; refinements consume real
    # copper and retain only still-provisional fixed controller interfaces.
    remaining_ports=[q for q in (live_ports or []) if q['net'] not in nets]
    with reserve_launches(remaining_ports):
        report['polish']=polish_matrix(board,protected_ids=stable,eligible_nets=nets,
            deadline=min(deadline-6,time.monotonic()+45),ports=remaining_ports)
        protected={uid for uid,t in snapshot(board)['tracks'].items() if t['net'] not in nets or t['locked']}|stable
        report['contact_pruning']=prune_matrix_contacts(board,protected_ids=protected,eligible_nets=nets,deadline=deadline-4)
        editable=set(snapshot(board)['tracks'])-protected
        removed=remove_covered(board,editable)
        cleanup=cleanup_generated(board,protected=protected,deadline=deadline)
        removed.extend(remove_covered(board,set(snapshot(board)['tracks'])-protected))
        # Field ownership is provisional until this receipt is captured. The
        # old pass keeps its feeder normalization/via behavior; only its
        # formerly protected field tracks enter this separate atomic phase.
        try:
            report['pre_receipt_hygiene']=clean_matrix_tracks(board,
                owned_ids=report['owned_ids'],source_ids=source_protected,nets=nets,deadline=deadline-4)
            removed.extend(report['pre_receipt_hygiene']['removed_ids'])
        except TimeoutError as error:
            # The helper restores every mutation before returning an expiry.
            # Keep the proved incumbent; final native rules still gate it.
            report['pre_receipt_hygiene']={'retained_incumbent':True,'reason':str(error),
                                          'removed_ids':[],'trimmed_ids':[]}
    after=snapshot(board);new_partitions=pad_partitions(board,deadline)
    if not preserves_contacts([list(g) for g in old_partitions],[list(g) for g in new_partitions]):
        raise ValueError('Matrix preparation lost an existing physical contact')
    if any(after['tracks'].get(u)!=t for u,t in before['tracks'].items() if t['locked'] or t['net'] not in nets and u not in participant_ids):
        raise ValueError('Matrix preparation changed immutable source copper')
    roles={uid:report['roles'].get(uid,('row_access' if family[t['net']]=='rows' else 'column_access'))
           for uid,t in after['tracks'].items() if t['net'] in nets}
    report.update(complete=True,controller_connected=True,scope='matrix-with-controller',roles=roles,
                  owned_ids=sorted(roles),terminals=required,physical_pad_count=len(required),
                  duplicate_cleanup={'removed_ids':removed},cleanup=cleanup,
                  source_copper_preserved=all(after['tracks'].get(u)==t for u,t in before['tracks'].items()),
                  immutable_source_copper_preserved=all(after['tracks'].get(u)==t for u,t in before['tracks'].items() if u in source_protected),
                  participant_ids=sorted(participant_ids),feeder_created_ids=sorted(feeder_ids),
                  retired_source_ids=sorted(set(before['tracks'])-set(after['tracks'])),
                  matrix_ports=[deepcopy(q) for q in live_ports if q['net'] in nets],source_ids=sorted(source_protected),
                  elapsed_s=time.monotonic()-start)
    return report


def prepare_matrix(board,cfg,*,row_layer,column_layer,deadline,ports=None,collectors=False,recovery_deadline=None,generated_heads=None,exit_bounds_nm=None,corridor_plan=None,participant_ownership=None):
    """Keep generated-head replacement atomic through polish and receipt handoff."""
    from copy import deepcopy
    from .interface_launches import current_launch_inventory
    before=snapshot(board) if generated_heads is not None or corridor_plan is not None or participant_ownership is not None else None
    if before is not None:
        from .conditional_banks import copy_native_tracks,_source_physics
        copies=copy_native_tracks(board)
        physics=_source_physics(board,set(before['tracks'])|{q['uuid'] for f in before['footprints'].values() for q in f['pads']})
    inventory=current_launch_inventory()
    launches=None if inventory is None else (deepcopy(inventory.ports),set(inventory.resolved),set(inventory.repair_reserved))
    accepted=False
    try:
        result=_prepare_matrix(board,cfg,row_layer=row_layer,column_layer=column_layer,deadline=deadline,
            ports=ports,collectors=collectors,recovery_deadline=recovery_deadline,
            generated_heads=generated_heads,exit_bounds_nm=exit_bounds_nm,corridor_plan=corridor_plan,**({'participant_ownership':participant_ownership} if participant_ownership is not None else {}))
        accepted=result.get('complete') is True and result.get('controller_connected') is True
        return result
    finally:
        if corridor_plan is not None and not accepted:corridor_plan.phase='rejected'
        if before is not None and not accepted:
            for item in list(board.GetTracks()):board.Delete(item)
            for clone in copies.values():board.Add(clone)
            pads={d.m_Uuid.AsString():d for f in board.GetFootprints() for d in f.Pads()}
            for footprint in before['footprints'].values():
                for q in footprint['pads']:pads[q['uuid']].SetNet(board.FindNet(q['net']))
            board.BuildConnectivity()
            if snapshot(board)!=before or _source_physics(board,set(before['tracks'])|{q['uuid'] for f in before['footprints'].values() for q in f['pads']})!=physics:
                raise ValueError('Matrix source replacement rollback changed its entry frame or native properties')
            if inventory is not None:
                inventory.ports,inventory.resolved,inventory.repair_reserved=launches
