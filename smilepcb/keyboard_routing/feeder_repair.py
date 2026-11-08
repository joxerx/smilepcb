"""Bounded rerouting of generated MCU feeders around an immutable key field."""
import time


def displaced_ids(before,live,*,removed,protected,nets):
    """Validate the local carve and identify only its already-removed blockers."""
    removed,protected,nets=set(removed),set(protected),set(nets)
    if not removed<=before.keys() or removed&protected:
        raise ValueError('Displacement did not identify editable source copper')
    if not protected<=before.keys() or not protected<=live.keys():
        raise ValueError('Displacement lost protected source copper')
    displaced={before[u]['net'] for u in removed}
    if not displaced<=nets:raise ValueError('Displacement crossed the matrix boundary')
    if removed&live.keys() or any(before[u]['locked'] for u in removed):
        raise ValueError('Displacement did not remove editable source copper')
    if any(live.get(u)!=t for u,t in before.items() if u not in removed):
        raise ValueError('Displacement changed unaffected source copper')
    return sorted(removed)


def _pad_state(graph,nets):
    from smilepcb.native_worker import p
    groups={n:set() for n in nets};seen=set()
    for i,node in enumerate(graph['nodes']):
        if node['net'] in groups and isinstance(node['item'],p.PAD) and node['item'].GetNumber():
            groups[node['net']].add(graph['find'](i));seen.add(node['item'].m_Uuid.AsString())
    counts={n:len(v) for n,v in groups.items()}
    return {'groups':counts,'pad_ids':seen,'missing':{n:v-1 for n,v in counts.items() if v!=1}}


def _pad_attached_ids(graph, nets):
    from smilepcb.native_worker import p
    roots = {graph['find'](i) for i, node in enumerate(graph['nodes'])
             if isinstance(node['item'], p.PAD) and node['item'].GetNumber()}
    return {node['item'].m_Uuid.AsString() for i, node in enumerate(graph['nodes'])
            if node['net'] in nets and graph['find'](i) in roots}


def _isolated_editable_components(graph, eligible):
    # Delete only complete components made entirely of positively owned
    # copper. A pad, zone, protected item, or unknown conductor vetoes it.
    retired = set()
    for group in graph['groups'].values():
        ids = {graph['nodes'][i]['item'].m_Uuid.AsString() for i in group}
        if ids <= eligible:
            retired.update(ids)
    return retired


def matrix_pad_state(board,nets,deadline):
    """One native read of numbered-pad connectivity for this exact board state."""
    from .native_graph import copper_graph
    return _pad_state(copper_graph(board,nets,deadline),nets)


def pending_matrix(board,nets,deadline):
    return matrix_pad_state(board,nets,deadline)['missing']


def _repair_interfaces(before,nets,missing,ports,sources,immutable,*,participant_ids=()):
    """Keep live advances; dormant interfaces can reopen at their real source."""
    from copy import deepcopy
    from .interface_launches import interface_identity
    def inventory(values):
        result={}
        for port in values:
            key=interface_identity(port)
            if not key or key in result:raise ValueError('Ambiguous physical repair interface inventory')
            result[key]=port
        return result
    current,original=inventory(ports),inventory(sources)
    if current.keys()!=original.keys():raise ValueError('Repair source/current physical inventories differ')
    pads={d['uuid']:d['net'] for f in before['footprints'].values() for d in f['pads']}
    tracks=before['tracks'];protected=set(immutable);selected=[];dormant=[]
    protected.update(u for u,t in tracks.items() if t['locked'] or t['net'] not in nets and u not in participant_ids)
    for key,source in original.items():
        port=current[key];net=source['net']
        if port['net']!=net or any(pads.get(u)!=net for u in key):raise ValueError('Repair changed a physical source binding')
        if any(u not in tracks or tracks[u]['net']!=net for u in source['added_ids']):raise ValueError('Missing immutable repair source copper')
        protected.update(source['added_ids'])
        live=net not in nets or net in missing
        guard=port if live else source
        if any(u not in tracks or tracks[u]['net']!=net for u in guard['added_ids']):raise ValueError('Missing live advanced repair interface')
        if live:protected.update(guard['added_ids'])
        else:dormant.append(net)
        selected.append(deepcopy(guard))
    return selected,protected,sorted(set(dormant))



def _advance_participant_epoch(before,after,ownership,nets,protected,emitted):
    """Contract soft authority and protect every unowned repair descendant."""
    old,new=before['tracks'],after['tracks']
    if (not isinstance(emitted,list) or len(emitted)!=len(set(emitted)) or
            set(emitted)!=new.keys()-old.keys()):
        raise ValueError('Repair has no exact emitted copper inventory')
    from .collector_preparation import transient_access_ids,contract_provisional_ownership
    soft=transient_access_ids(before,ownership,nets)
    if (before['footprints']!=after['footprints'] or not set(protected)<=old.keys() or
            any(new.get(u)!=old[u] for u in protected)):
        raise ValueError('Repair changed hard copper or physical pads')
    editable={u for u,q in old.items() if q['net'] in nets and not q['locked'] and u not in protected}
    if any(new.get(u)!=q for u,q in old.items() if u not in editable|soft):
        raise ValueError('Repair changed copper outside its old authority')
    narrowed=contract_provisional_ownership(after,ownership,nets)
    fresh={u for u in emitted if new[u]['net'] in nets and not new[u]['locked']
        and new[u]['class'] in ('PCB_TRACK','PCB_VIA')}
    permitted={u for u in editable if new.get(u)==old[u]}|fresh|set(narrowed.tracks)
    return narrowed,(set(new)-permitted)|set(protected)


def repair_feeders(board, nets, protected, *, deadline, ports=None, source_ports=None, target_order=None, participant_ownership=None):
    """Repair a group or start with individual physical targets within one clock."""
    from .interface_launches import current_launches, interface_identity

    nets, protected = set(nets), set(protected)
    initial_ports = {interface_identity(port): port for port in current_launches()}
    initial_ports.update((interface_identity(port), port) for port in (ports or ()))
    sources = source_ports if source_ports is not None else list(initial_ports.values())
    participant_options={'participant_ownership':participant_ownership} if participant_ownership is not None else {}
    if target_order is None:
        history = _repair_feeders(board, nets, protected, deadline=deadline,
                                  ports=ports, source_ports=sources,**participant_options)
        pending = dict(history[-1].get('retained_pending', {})) if history else {}
        if len(pending) < (1 if participant_ownership is not None else 2):
            return history
        if participant_ownership is not None and len(pending)==1:
            rejected=history[-1];after=rejected.get('after')
            if (after is None or set(pending)&after.keys()
                    or not after.keys()-rejected.get('before',{}).keys()
                    or not rejected.get('plan',{}).get('removed')):
                return history  # Same scope without a witnessed new blocker is no new experiment.
        choices = sorted(pending, key=lambda net: (pending[net], net))[:3]
    else:
        if deadline - time.monotonic() <= 12:
            return []
        history = []
        target_order = tuple(dict.fromkeys(target_order))
        pending = pending_matrix(board, nets, deadline)
        # Leave room for one different approach when a neighbour cannot be restored.
        limit = max(1, int((deadline - time.monotonic()) / 32))
        choices = sorted((net for net in target_order if net in pending), key=pending.get)[:limit]

    # Keep other open nets fixed during each repair and restore all closed groups.
    trial_ports = None if any(row['accepted'] for row in history) else ports
    tried=set()
    while choices:
        epoch_progress=False
        for index, net in enumerate(choices):
            if target_order is not None or participant_ownership is not None:
                if deadline-time.monotonic()<=12:break
                # A preceding repair may incidentally close another target.
                if index:pending = pending_matrix(board, nets, deadline)
                if net in tried or net not in pending:continue
                tried.add(net)
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 12:
                break
            scope = (nets - pending.keys()) | {net}
            other_pending = {key: count for key, count in pending.items() if key not in scope}
            cutoff = now + remaining / (len(choices) - index)
            max_attempts = 3 if target_order is not None or participant_ownership is not None else 1
            trial, blocker_ids, blocker_nets = [], set(), set()
            # The group trial already witnessed an incumbent that could not
            # be restored. Reuse its exact-rollback evidence for this target.
            rejected=history[-1] if participant_ownership is not None and history else {}
            after=rejected.get('after')
            if after is not None and net not in after:
                reopened=after.keys()-rejected.get('before',{}).keys()
                if reopened:
                    from smilepcb.native_worker import snapshot
                    restored=snapshot(board)['tracks']
                    blocker_ids={u for u in rejected.get('plan',{}).get('removed',())
                        if u in restored and restored[u]['net'] in reopened}
                    blocker_nets={restored[u]['net'] for u in blocker_ids}
            for attempt in range(max_attempts):
                now = time.monotonic()
                available = cutoff - now
                if available <= 12:
                    break
                may_retry = attempt + 1 < max_attempts and available >= 32
                search_share=max(24,min(78,available/2)) if participant_ownership is not None else 24
                stop = min(cutoff - 16, now + search_share) if may_retry else cutoff
                if participant_ownership is not None:
                    from smilepcb.native_worker import snapshot
                    trial_before=snapshot(board)
                rows = _repair_feeders(board, scope, protected | blocker_ids, deadline=stop,
                                       ports=trial_ports, source_ports=sources,**participant_options)
                for row in rows:
                    if blocker_nets:
                        row['protected_incumbents'] = sorted(blocker_nets)
                trial.extend(rows)
                if not may_retry or not rows or rows[-1]['accepted']:
                    break
                rejected = rows[-1]
                after = rejected.get('after')
                if after is None or net in after:
                    break
                reopened = after.keys() - rejected['before'].keys()
                removed = set(rejected.get('plan', {}).get('removed', ())) - blocker_ids
                if not reopened or not removed:
                    break
                from smilepcb.native_worker import snapshot
                restored = snapshot(board)['tracks']
                new_blockers = {uid for uid in removed if restored[uid]['net'] in reopened}
                if not new_blockers:
                    break
                blocker_ids.update(new_blockers)
                blocker_nets.update(restored[uid]['net'] for uid in new_blockers)
            for row in trial:
                row['single_target'] = net
                for key in ('before', 'after', 'retained_pending'):
                    if key in row:
                        row[key] = {**other_pending, **row[key]}
                if row['accepted']:
                    pending = dict(row['after'])
                    trial_ports = None
            history.extend(trial)
            if participant_ownership is not None and any(row['accepted'] for row in trial):
                # Only real global closure can authorize another epoch. New
                # nonmatrix bodies become hard; old ownership only contracts.
                accepted_row=next(row for row in reversed(trial) if row['accepted'])
                try:
                    if deadline-time.monotonic()<=12:raise TimeoutError('No participant epoch proof reserve')
                    actual_pending=pending_matrix(board,nets,deadline)
                    before_pending={**other_pending,**accepted_row['before']}
                    if sum(actual_pending.values())>=sum(before_pending.values()):
                        raise ValueError('No global matrix progress for another participant epoch')
                    owner,hard=_advance_participant_epoch(trial_before,snapshot(board),participant_ownership,
                        nets,protected,accepted_row.get('actual_emitted_ids'))
                except (ValueError,TimeoutError) as error:
                    history[-1].update(participant_epoch_ended=True,participant_epoch_reason=str(error))
                    return history
                participant_ownership,protected=owner,hard
                participant_options={'participant_ownership':owner}
                pending=actual_pending;accepted_row['after']=dict(pending)
                accepted_row['participant_epoch_advanced']=True
                accepted_row['participant_owned_remaining']=len(owner.tracks)
                epoch_progress=True;tried.clear()
                if not pending:return history
        if deadline-time.monotonic()<=12:break
        if target_order is None:
            if participant_ownership is None or not epoch_progress:break
            choices=sorted(pending,key=lambda net:(pending[net],net))[:3]
            continue
        # The estimate sizes one wave, not a permanent exclusion. Reassess
        # actual closure and time only after preserving that wave's cutoffs.
        pending=pending_matrix(board,nets,deadline)
        limit=max(1,int((deadline-time.monotonic())/32))
        choices=sorted((net for net in target_order if net in pending and net not in tried),key=pending.get)[:limit]
    return history



def _restore_participant_partitions(board,partitions,affected,pad_nets,matrix_nets,factory,*,deadline):
    from .completion import pad_partitions
    rows=[];added=set()
    def connected(group,current):return any(set(group)<=set(other) for other in current)
    ordered=sorted(partitions,key=lambda g:(pad_nets[next(iter(g))] not in matrix_nets,-len(g),sorted(g)))
    current=pad_partitions(board,deadline)
    for group in ordered:
        names={pad_nets[u] for u in group}
        if len(names)!=1:raise ValueError('Recovery partition crosses physical nets')
        net=next(iter(names))
        if net not in affected or connected(group,current):continue
        row={'phase':'displaced_partition','net':net,'required_pad_ids':sorted(group),'history':[]};rows.append(row)
        for grid in (500000,125000):
            if deadline-time.monotonic()<=2:break
            router=factory(board,deadline=min(deadline,time.monotonic()+22),compact=True)
            router.grid_step=grid;router.connection_budget_s=20;router.search_budget_s=20;router.routing_penalty=None
            from .native_graph import route_components
            count=route_components(board,router,{net},required_pad_ids=set(group),refresh_components=True,prefer_small_goal=True)
            added.update(getattr(router,'added',()))
            row['history'].append({'grid_nm':grid,'connections':count,'graph':getattr(router,'graph_diagnostic',{})})
            current=pad_partitions(board,deadline)
            row['restored']=connected(group,current)
            if row['restored']:break
    return rows,added


def _repair_feeders(board,nets,protected,*,deadline,ports=None,source_ports=None,participant_ownership=None):
    from copy import deepcopy
    from smilepcb.native_worker import snapshot
    from .matrix import KeyboardRouter
    from .native_graph import negotiate_routes,route_components,copper_graph
    from .interface_launches import reserve_launches,current_launches,current_launch_inventory,resolved_interfaces,interface_identity,publish_launches
    history=[];immutable=set(protected)
    participant_ids=set();covered=set(nets);Router=KeyboardRouter
    if participant_ownership is not None:
        if deadline-time.monotonic()<=8:return []
        from .collector_preparation import transient_access_ids
        from .local_router import LocalRouter
        frame=snapshot(board);participant_ids=transient_access_ids(frame,participant_ownership,nets)-immutable
        covered.update(frame['tracks'][u]['net'] for u in participant_ids);Router=LocalRouter

    inherited={interface_identity(q):q for q in current_launches()}
    current=deepcopy(list((inherited|{interface_identity(q):q for q in (ports or [])}).values()))
    sources=deepcopy(list((inherited|{interface_identity(q):q for q in (source_ports if source_ports is not None else current)}).values()))
    guards=current;resolved=set();policy=None
    def factory(candidate,**kw):
        r=Router(candidate,track_width_nm=candidate.GetDesignSettings().m_TrackMinWidth or 150000,**kw)
        if policy is not None:r.port_policy=policy
        r.connection_budget_s=10;r.search_budget_s=10;r.heuristic_weight=3
        r.via_cost_nm=1000000;r.layer_change_heuristic_nm=0;r.displacement_cost_nm=1000000
        return r
    from smilepcb.native_worker import p
    from .completion import pad_partitions
    from .handoff import preserves_contacts
    for _ in range(1 if participant_ownership is not None else 3):
        # Admission must precede even a read-only native graph: the caller
        # may have spent the repair sub-budget while routing the feeders.
        if deadline-time.monotonic()<=8:break
        graph=copper_graph(board,covered|{q['net'] for q in current},deadline)
        missing=_pad_state(graph,nets)['missing']
        if not missing:break
        started=time.monotonic();stop=min(deadline-2,started+78);before=snapshot(board)
        guards,retained,dormant=_repair_interfaces(before,nets,missing,current,sources,immutable,participant_ids=participant_ids)
        resolved=resolved_interfaces(guards,set(),graph) if graph is not None else set()
        if not retained<=before['tracks'].keys():raise ValueError('Missing protected repair copper')
        partitions=pad_partitions(board,deadline)
        attached = _pad_attached_ids(graph, covered)
        copies={}
        for t in board.GetTracks():
            copy=t.Duplicate();copy.SetUuid(p.KIID(t.m_Uuid.AsString()));copies[t.m_Uuid.AsString()]=copy
        accepted=False;row={'before':missing,'accepted':False,'dormant_interface_nets':dormant,
                           'protected_live_ids':sorted(retained-immutable)}
        try:
            # A nested inventory replaces stale outer advanced terminals before
            # any closed feeder can be displaced. Its original source objects
            # remain immutable, and every still-open advance remains live.
            with reserve_launches(guards):
                from .port_policy import PortPolicy
                # Every oracle shares the immutable interface objects and the
                # same lifecycle, including joins made during a relaxed plan.
                policy=PortPolicy(board,guards) if guards else None
                if policy is not None:policy.set_resolved(resolved)
                current_launch_inventory().resolved=set(resolved)
                plan_stop=min(stop-2,started+min(30,(stop-started)*.4))
                local=dict(before['tracks']);origins={u:u for u in local}
                removed_originals=set();displaced=set();proposals=[];plans=[];reopened=set()
                interface_keys={interface_identity(q) for q in guards if q['net'] in nets}
                while True:
                    # A follow-up negotiation builds two native oracles before
                    # its first graph. Apply the same admission floor as a
                    # fresh pass; restoration still owns the remaining time.
                    if plans:
                        remaining_plan=plan_stop-time.monotonic()
                        if remaining_plan<=8:
                            row['replan_admission_remaining_s']=max(0,remaining_plan)
                            break
                    entry=snapshot(board)['tracks'];closed=set(resolved)
                    plan=negotiate_routes(board,set(missing),retained,plan_stop,router_factory=factory)
                    plans.append(plan);row['negotiations']=plans
                    live=snapshot(board)['tracks']
                    removed=displaced_ids(entry,live,removed=plan['removed'],protected=retained,nets=covered)
                    if not set(removed)<=origins.keys():raise ValueError('Displacement lost its source ancestry')
                    displaced.update(entry[u]['net'] for u in removed)
                    fragments=set(plan.get('retained_fragments',()))
                    ancestry=plan.get('fragment_sources',{})
                    if (set(ancestry)!=fragments or not fragments<=live.keys() or fragments&local.keys()
                            or any(parent not in removed for parent in ancestry.values())
                            or any(any(live[u][key]!=entry[parent][key] for key in ('net','layer','width','class'))
                                   for u,parent in ancestry.items())):
                        raise ValueError('Negotiation lost its native-clear fragment ancestry')
                    parents={u:origins[parent] for u,parent in ancestry.items()}
                    removed_originals.update(origins[u] for u in removed)
                    for u in removed:local.pop(u);origins.pop(u)
                    local.update({u:live[u] for u in fragments});origins.update(parents)
                    proposal=set(plan.get('added',()));proposals.extend(plan.get('added',()))
                    if (not proposal<=live.keys() or proposal&entry.keys()
                            or any(live[u]['net'] not in missing for u in proposal)):
                        raise ValueError('Negotiation lost its provisional path inventory')
                    if not displaced:break
                    # Carving can reopen a completed source escape. Retain the
                    # native-clear witness unless its guard context changed.
                    graph=copper_graph(board,covered,stop)
                    if guards:
                        resolved=resolved_interfaces(guards,resolved,graph)
                        policy.set_resolved(resolved)
                        current_launch_inventory().resolved=set(resolved)
                    newly_reopened=((closed-resolved)&interface_keys)-reopened
                    reopened.update(newly_reopened)
                    if not newly_reopened:break
                    if participant_ownership is not None and callable(getattr(policy,'_launch_conflict',None)):
                        witnesses=[t for t in board.GetTracks() if t.m_Uuid.AsString() in proposal]
                        conflict=any(policy._launch_conflict(t.GetEffectiveShape(layer),layer,t.GetNetname())
                            for t in witnesses for layer in (p.F_Cu,p.B_Cu) if t.IsOnLayer(layer))
                        if not conflict:
                            row['reopened_guards_clear_of_witness']=True
                            break

                    for t in list(board.GetTracks()):
                        if t.m_Uuid.AsString() in proposal:board.Delete(t)
                    # Removing the witness can make its own target live again;
                    # refresh before another oracle uses the shared policy.
                    graph=copper_graph(board,covered,stop)
                    resolved=resolved_interfaces(guards,resolved,graph)
                    policy.set_resolved(resolved)
                    current_launch_inventory().resolved=set(resolved)
                    # The finite physical inventory bounds replanning. No pass
                    # gets a new deadline or retries the same reopening event.
                    if time.monotonic()>=plan_stop:break
                remaining={u for u in local if u not in before['tracks']}
                plan=dict(plan,removed=sorted(removed_originals),added=proposals,
                          retained_fragments=sorted(remaining),fragment_sources={u:origins[u] for u in remaining},
                          displaced_length_mm=sum(copies[u].GetLength() for u in removed_originals if copies[u].GetClass()=='PCB_TRACK')/1e6,
                          retained_length_mm=sum(t.GetLength() for t in board.GetTracks() if t.m_Uuid.AsString() in remaining)/1e6)
                row['plan']=plan
                if not plan['connected'] and not removed_originals:
                    row.update(after=missing,reason='No negotiated connection');break
                if displaced:row['replanned_proposal_ids']=proposals
                attempts=[];created=set(plan.get('added',()))|set(plan.get('retained_fragments',()))
                grids=(500000,250000,125000)
                # Finish the missing-net ladder before an incumbent can heal
                # its old crossing. A later fine success must not leave every
                # incumbent trapped in that grid's already-spent subwindow.
                for phase,selected in (('missing',set(missing)),('displaced',set() if participant_ownership is not None else displaced)):
                    if not selected:continue
                    for resolution,grid in enumerate(grids):
                        if guards:
                            graph=copper_graph(board,covered,deadline)
                            active_missing=_pad_state(graph,nets)['missing']
                            # Fixed copper is immutable, so retain its exact entry
                            # closure while rederiving every changed matrix net.
                            resolved=resolved_interfaces(guards,resolved,graph)
                            policy.set_resolved(resolved)
                            current_launch_inventory().resolved=set(resolved)
                        else:active_missing=pending_matrix(board,nets,deadline)
                        restore=set(active_missing)&selected
                        if not restore or stop-time.monotonic()<2:break
                        router=factory(board,deadline=min(stop-1,time.monotonic()+15),compact=True)
                        router.grid_step=grid;router.connection_budget_s=6;router.search_budget_s=6
                        router.heuristic_weight=5;router.routing_penalty=None
                        count=route_components(board,router,restore,finer_grid_steps=grids[resolution+1:])
                        created.update(getattr(router,'added',()))
                        attempts.append({'grid_nm':grid,'phase':phase,'connections':count,'graph':getattr(router,'graph_diagnostic',{})})
                if participant_ownership is not None and displaced:
                    pad_nets={q['uuid']:q['net'] for f in before['footprints'].values() for q in f['pads']}
                    rows,added=_restore_participant_partitions(board,partitions,displaced,pad_nets,nets,factory,deadline=stop-1)
                    attempts.extend(rows);created.update(added)
                graph=copper_graph(board,covered,deadline)
                after=_pad_state(graph,nets)['missing']
                if guards:
                    resolved=resolved_interfaces(guards,resolved,graph)
                    policy.set_resolved(resolved);current_launch_inventory().resolved=set(resolved)
                contacts=preserves_contacts([list(g) for g in partitions],[list(g) for g in pad_partitions(board,deadline)])
                final=snapshot(board)
                exact=final['footprints']==before['footprints'] and all(final['tracks'].get(u)==before['tracks'][u] for u in retained)
                local_exact=all(final['tracks'].get(u)==t for u,t in local.items())
                required_contacts = {uid for uid in local if origins[uid] in attached}
                disconnected = required_contacts - _pad_attached_ids(graph, covered)
                improvement = not after if participant_ownership is not None else sum(after.values()) < sum(missing.values())
                retired = set()
                if contacts and exact and local_exact and improvement and (disconnected or participant_ownership is not None):
                    # A replacement can close every pad while leaving unused
                    # pieces of a displaced feeder. Retire a detached island
                    # only if all its members were previously attached and
                    # belong to this pass's editable displaced copper.
                    eligible = {uid for uid in disconnected - retained
                                if local[uid]['net'] in displaced and not local[uid]['locked']
                                and local[uid]['class'] in ('PCB_TRACK', 'PCB_VIA')}
                    if participant_ownership is not None:eligible.update(created-retained)
                    retired = _isolated_editable_components(graph, eligible)
                    if retired:
                        for track in list(board.GetTracks()):
                            if track.m_Uuid.AsString() in retired:
                                board.Delete(track)
                        graph = copper_graph(board, covered, deadline)
                        if _pad_state(graph, nets)['missing'] != after or not preserves_contacts(
                                [list(g) for g in partitions], [list(g) for g in pad_partitions(board, deadline)]):
                            raise ValueError('Retiring isolated copper changed a pad connection')
                        disconnected = required_contacts - retired - _pad_attached_ids(graph, covered)
                local_contacts = not disconnected
                new_contacts = participant_ownership is None or not (
                    ((created-retired)&set(snapshot(board)['tracks']))-_pad_attached_ids(graph,covered))
                accepted = contacts and exact and local_exact and local_contacts and new_contacts and improvement
                if accepted and participant_ownership is not None:
                    actual=set(snapshot(board)['tracks'])-set(before['tracks'])
                    # Every candidate/fragment/restoration creator contributes
                    # to `created`; a bare current-minus-hard set is no proof.
                    if actual<=created:row['actual_emitted_ids']=sorted(actual)
                row.update(retired_feeder_ids=sorted(retired),local_copper_exact=local_exact,retained_contacts_preserved=local_contacts,new_copper_contacts_preserved=new_contacts,restoration=attempts,after=after,accepted=accepted,
                           contacts_preserved=contacts,protected_exact=exact,
                           reason=None if accepted else 'No connectivity improvement with all incumbent contacts restored')
        except (ValueError,TimeoutError) as error:
            row.update(reason=str(error),accepted=False)
        finally:
            if not accepted:
                live=snapshot(board)['tracks']
                for t in list(board.GetTracks()):
                    uid=t.m_Uuid.AsString()
                    if uid not in before['tracks'] or live[uid]!=before['tracks'][uid]:board.Delete(t)
                present={t.m_Uuid.AsString() for t in board.GetTracks()}
                for uid,copy in copies.items():
                    if uid not in present:board.Add(copy)
                board.BuildConnectivity()
                if snapshot(board)!=before:raise ValueError('Feeder repair rollback changed incumbent geometry')
                row['retained_pending']=missing
            row['elapsed_s']=time.monotonic()-started;history.append(row)
        if not accepted:break
        current=deepcopy(guards)
        publish_launches(current,resolved=resolved)
    return history
