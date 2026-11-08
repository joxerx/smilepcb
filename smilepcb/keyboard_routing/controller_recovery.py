"""Close actual MCU-local functions before reserving matrix source banks.

Only positively copied compiled supply copper may participate in negotiation.
All old physical pad partitions and fixed source geometry remain mandatory.
"""
from types import SimpleNamespace
import time



class ControllerRecovery(dict):
    """JSON carries evidence only; an internal creator owns the Native lease."""
    controller_ownership=None


def _recovered_local_ownership(t,board,cfg,entry,report,*,deadline):
    from copy import deepcopy
    from .collector_preparation import ProvisionalOwnership
    from .generation import current_generation_profile
    if report.get('accepted') is not True:raise ValueError('Recovery ownership requires accepted physical closure')
    after=t.snapshot(board)
    if after['footprints']!=entry['footprints']:raise ValueError('Recovery ownership changed physical pads')
    phases=dict(t.ordered_nets(cfg));excluded=set(phases['rows']+phases['columns'])|set(cfg['plane_nets'])|{'USB_DP','USB_DM'}
    excluded.update(entry['tracks'][u]['net'] for u in report.get('positive_compiled_supply_ids',()) if u in entry['tracks'])
    parts={q['ref']:q for q in cfg['parts']};closed=report['local_closed']['nets']
    names={net for net in closed if net not in excluded and not net.startswith('+')}
    pads={d['uuid']:(f['ref'],d) for f in after['footprints'].values() for d in f['pads'] if d['number'] and d['net'] in names}
    for net in names:
        actual={u:{'ref':ref,'pad':d} for u,(ref,d) in pads.items() if d['net']==net}
        if (actual!=closed[net]['pads'] or not any(q['ref']=='U_MCU' for q in actual.values()) or
                any(parts.get(q['ref'],{}).get('region')!='controller' or
                    parts[q['ref']]['nets'].get(q['pad']['number'])!=net for q in actual.values())):
            raise ValueError('Recovered local ownership differs from actual closed controller pads')
    fresh={u:q for u,q in after['tracks'].items() if u not in entry['tracks'] and q['net'] in names and not q['locked']}
    traced=set(report.get('additive',{}).get('added_ids',()))
    for row in report.get('attempts',()):traced.update(row.get('negotiate',{}).get('added',()))
    if not set(fresh)<=traced:raise ValueError('Recovery local copper lacks creator trace')
    if any(q['class'] not in ('PCB_TRACK','PCB_VIA') for q in fresh.values()):raise ValueError('Unsupported local ownership conductor')
    if fresh:
        graph=t.graph(board,{q['net'] for q in fresh.values()},deadline,include_zones=False)
        ids={n['item'].m_Uuid.AsString():i for i,n in enumerate(graph['nodes'])};roots={}
        for u,(ref,d) in pads.items():
            if d['net'] not in {q['net'] for q in fresh.values()}:continue
            if u not in ids:raise ValueError('Recovered local ownership lost a physical pad')
            roots.setdefault(d['net'],set()).add(graph['find'](ids[u]))
        if any(len(v)!=1 for v in roots.values()) or any(u not in ids or graph['find'](ids[u]) not in roots.get(q['net'],set()) for u,q in fresh.items()):
            raise ValueError('Recovered local copper is not attached to its controller pads')
    if time.monotonic()>=deadline:raise TimeoutError('Recovered local ownership proof deadline exhausted')
    owned_nets={q['net'] for q in fresh.values()}
    return ProvisionalOwnership(current_generation_profile().name,deepcopy(fresh),
        {u:deepcopy(d) for u,(ref,d) in pads.items() if d['net'] in owned_nets})


def restore_tracks(board, frame, copies):
    """Restore changed/deleted Native copper, retaining unchanged object handles."""
    from smilepcb.native_worker import p, snapshot
    def exact(item, template):
        if item.GetLocalClearance()!=template.GetLocalClearance():return False
        if isinstance(item,p.PCB_VIA) and item.GetEffectiveHoleShape().Format(False)!=template.GetEffectiveHoleShape().Format(False):return False
        return all(item.IsOnLayer(face)==template.IsOnLayer(face) and (not item.IsOnLayer(face) or
            item.GetEffectiveShape(face).Format(False)==template.GetEffectiveShape(face).Format(False)) for face in (p.F_Cu,p.B_Cu))
    live=snapshot(board)['tracks']
    for item in list(board.GetTracks()):
        uid=item.m_Uuid.AsString()
        if uid not in frame['tracks'] or live[uid]!=frame['tracks'][uid] or not exact(item,copies[uid]):board.Delete(item)
    present={item.m_Uuid.AsString() for item in board.GetTracks()}
    for uid,template in copies.items():
        if uid not in present:
            clone=template.Duplicate();clone.SetUuid(p.KIID(uid));board.Add(clone)
    board.BuildConnectivity()
    if snapshot(board)['tracks']!=frame['tracks']:raise RuntimeError('Controller recovery could not restore exact copper')


def _tools():
    from smilepcb.native_worker import snapshot
    from smilepcb.keyboard_phases import ordered_nets
    from smilepcb.keyboard_native import verify_usb_pair
    from .controller_local import prepare_controller_local,capture_local_closures,verify_local_closures,_members
    from .interface_contract import mcu_signal_nets
    from .native_graph import copper_graph,negotiate_routes
    from .local_router import LocalRouter
    from .completion import pad_partitions,partitions_preserved
    from .feeder_repair import _restore_participant_partitions
    from .conditional_geometry import prove_copper,copy_native_tracks
    from .safety import require_via_land_clearance
    from .cleanup import remove_tails
    return SimpleNamespace(snapshot=snapshot,ordered_nets=ordered_nets,usb=verify_usb_pair,
        prepare=prepare_controller_local,capture=capture_local_closures,verify=verify_local_closures,
        members=_members,signal_nets=mcu_signal_nets,graph=copper_graph,negotiate=negotiate_routes,
        router=LocalRouter,partitions=pad_partitions,preserved=partitions_preserved,
        restore=_restore_participant_partitions,prove=prove_copper,copy_tracks=copy_native_tracks,
        restore_tracks=restore_tracks,via_clearance=require_via_land_clearance,cleanup=remove_tails)


def recover_controller_locals(board,cfg,*,module_power_ids,deadline,usb_max_length_nm=...):
    """Use the same negotiated joins as normal routing, before source freezing."""
    t=_tools();entry=t.snapshot(board);soft=set(module_power_ids)
    if not soft<=entry['tracks'].keys() or any(entry['tracks'][u]['locked'] or not (
        entry['tracks'][u]['net'] in cfg['plane_nets'] or entry['tracks'][u]['net'].startswith('+')) for u in soft):
        raise ValueError('Invalid positively copied compiled supply scope')
    copies=t.copy_tracks(board);initial_soft=set(soft);start=time.monotonic()
    report=ControllerRecovery(accepted=False,attempts=[],positive_compiled_supply_ids=sorted(soft))
    try:
        report['additive']=t.prepare(board,cfg,deadline=deadline)
        phases=dict(t.ordered_nets(cfg));matrix=set(phases['rows']+phases['columns'])
        _,parts,members=t.members(board,cfg)
        required={n for n in t.signal_nets(cfg)-matrix if members.get(n) and
            len(members[n])>=2 and all(parts[d['ref']]['region']=='controller' for d in members[n].values())}
        tried=set()
        while True:
            closed=t.capture(board,cfg,deadline=deadline);pending=required-set(closed['nets'])
            if not pending:break
            available=pending-tried
            if not available or deadline-time.monotonic()<20:raise ValueError('Local controller obligations remain: '+str(sorted(pending)))
            net=min(available,key=lambda n:(len(members[n]),n));tried.add(net)
            before=t.snapshot(board);prior=t.partitions(board,deadline)
            pads={d['uuid']:d['net'] for f in before['footprints'].values() for d in f['pads']}
            supplies={before['tracks'][u]['net'] for u in soft};hard=set(before['tracks'])-soft
            row={'net':net,'required_pad_ids':sorted(members[net]),'accepted':False};report['attempts'].append(row)
            tick=time.monotonic();stop=min(deadline-5,tick+80)
            row['negotiate']=t.negotiate(board,{net},hard,stop-25,required_pad_ids=set(members[net]))
            graph=t.graph(board,{net},stop,include_zones=False)
            ids={q['item'].m_Uuid.AsString():i for i,q in enumerate(graph['nodes'])}
            if not set(members[net])<=ids.keys() or len({graph['find'](ids[u]) for u in members[net]})!=1:
                raise ValueError('Local signal remains split')
            row['restore'],_=t.restore(board,prior,supplies,pads,set(),t.router,deadline=stop-3)
            if not t.preserved(prior,t.partitions(board,stop)):raise ValueError('Prior physical pad partition lost')
            after=t.snapshot(board)
            if after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=before['tracks'][u] for u in hard):
                raise ValueError('Fixed controller source geometry changed')
            delta={u for u,q in after['tracks'].items() if before['tracks'].get(u)!=q}
            removed=set(before['tracks'])-set(after['tracks'])
            if not removed<=soft:raise ValueError('Removed unowned controller copper')
            nets={after['tracks'][u]['net'] for u in delta}
            if not nets<=supplies|{net}:raise ValueError('Controller repair emitted unrelated copper')
            graph=t.graph(board,nets,stop,include_zones=False)
            if t.prove(board,delta,graph,nets,stop)!=len(delta):raise ValueError('Controller repair lacks copper proof')
            t.via_clearance(board,deadline=stop)
            now=t.capture(board,cfg,deadline=stop)
            if net not in now['nets']:raise ValueError('Controller-local closure not proved')
            t.verify(board,cfg,closed,deadline=stop);t.usb(board,max_length_nm=usb_max_length_nm)
            if time.monotonic()>=stop:raise TimeoutError('Controller-local acceptance deadline exhausted')
            descendants={u for u in delta if after['tracks'][u]['net'] in supplies}
            soft=(soft-removed)|descendants
            row.update(accepted=True,removed_ids=sorted(removed),power_descendants=sorted(descendants),elapsed_s=time.monotonic()-tick)
        # Carving preserves clear fragments. Retire their free ends while the
        # positively owned supplies are still editable, before source freezing.
        clean_before=t.snapshot(board);prior=t.partitions(board,deadline)
        hard=set(clean_before['tracks'])-soft
        if deadline-time.monotonic()<5:raise TimeoutError('No controller-tail cleanup proof allowance')
        report['cleanup']=t.cleanup(board,protected=hard,budget_s=min(10,deadline-time.monotonic()-4))
        clean_after=t.snapshot(board)
        removed=set(clean_before['tracks'])-set(clean_after['tracks'])
        changed={u for u,q in clean_after['tracks'].items() if clean_before['tracks'].get(u)!=q}
        if (clean_after['footprints']!=clean_before['footprints'] or not removed|changed<=soft
                or removed!=set(report['cleanup']['removed_ids']) or changed!=set(report['cleanup']['trimmed_ids'])
                or any(clean_after['tracks'].get(u)!=clean_before['tracks'][u] for u in hard)
                or not t.preserved(prior,t.partitions(board,deadline))):
            raise ValueError('Controller supply cleanup changed fixed copper or prior physical contacts')
        if changed:
            nets={clean_after['tracks'][u]['net'] for u in changed}
            graph=t.graph(board,nets,deadline,include_zones=False)
            if t.prove(board,changed,graph,nets,deadline)!=len(changed):raise ValueError('Controller supply trim lacks Native proof')
        t.verify(board,cfg,closed,deadline=deadline);t.usb(board,max_length_nm=usb_max_length_nm)
        t.via_clearance(board,deadline=deadline);soft.intersection_update(clean_after['tracks'])
        final=t.capture(board,cfg,deadline=deadline);t.verify(board,cfg,final,deadline=deadline)
        after=t.snapshot(board)
        if not required<=set(final['nets']) or after['footprints']!=entry['footprints'] or any(
            after['tracks'].get(u)!=q for u,q in entry['tracks'].items() if u not in initial_soft):
            raise ValueError('Controller-local final invariant failed')
        if time.monotonic()>=deadline:raise TimeoutError('Controller-local final proof deadline exhausted')
        candidate=dict(report,accepted=True,local_closed=final,power_descendant_ids=sorted(soft))
        owner=_recovered_local_ownership(t,board,cfg,entry,candidate,deadline=deadline)
        report.update(candidate);report.controller_ownership=owner
    except (ValueError,TimeoutError) as error:
        report.update(accepted=False,reason=str(error));report.controller_ownership=None
    finally:
        report['elapsed_s']=time.monotonic()-start
        if not report['accepted']:t.restore_tracks(board,entry,copies)
    return report
