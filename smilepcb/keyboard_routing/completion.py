"""Bounded geometric completion on a disposable native board.

No file is saved here. The native caller saves only an accepted candidate;
full native ERC/DRC and firmware acceptance belong to the build service.
"""
from collections import defaultdict
import time
from smilepcb.native_worker import p,snapshot
from .local_router import LocalRouter,xy,distance,vec
from .native_graph import copper_graph,route_components,stitch_zones
from .safety import require_via_land_clearance


def pad_partitions(board,deadline):
    nets={d.GetNetname() for f in board.GetFootprints() for d in f.Pads() if d.GetNumber() and d.GetNetname()}
    graph=copper_graph(board,nets,deadline);groups=defaultdict(set)
    for i,node in enumerate(graph['nodes']):
        d=node['item']
        if isinstance(d,p.PAD) and d.GetNumber():groups[node['net'],graph['find'](i)].add(d.m_Uuid.AsString())
    return list(groups.values())


def require_provisional_frame(before,after,protected):
    """Permit disposable routing edits while retaining the actual native frame."""
    protected=set(protected)
    if not protected<=before['tracks'].keys():raise ValueError('Missing protected source copper')
    immutable=protected|{uid for uid,shape in before['tracks'].items() if shape['locked']}
    if before['footprints']!=after['footprints']:
        raise ValueError('Provisional routing changed footprint or pad identities')
    if any(after['tracks'].get(uid)!=before['tracks'][uid] for uid in immutable):
        raise ValueError('Provisional routing changed protected or locked copper')
    return True


def partitions_preserved(before,after):
    return all(any(group<=other for other in after) for group in before)


def component_frontiers(graph,indices,deadline):
    """Find exposed conductor ends on their actual reachable copper faces."""
    nodes=[graph['nodes'][i] for i in indices if not graph['nodes'][i]['zone']]
    result=set()
    for node in nodes:
        item=node['item']
        for layer,shape in node['shapes'].items():
            for point in node['points']:
                if time.monotonic()>=deadline:raise TimeoutError('Frontier discovery deadline exhausted')
                probe=shape if isinstance(item,(p.PAD,p.PCB_VIA)) else p.SHAPE_CIRCLE(vec(point),1)
                if not any(other is not node and layer in other['shapes'] and
                           p.SHAPE.Collide(probe,other['shapes'][layer],0) for other in nodes):
                    result.add(point)
    # A closed local copper loop may have no exposed endpoint; its pad is
    # still an eligible origin for a new branch or a checked layer access.
    return sorted(result or {xy(node['item'].GetPosition()) for node in nodes if isinstance(node['item'],p.PAD)})


def detour_candidates(board,*,protected,deadline):
    from smilepcb.keyboard_native import pad_gaps
    missing=set(pad_gaps(board));graph=copper_graph(board,missing,deadline);rows={}
    pad_groups=[]
    for indices in graph['groups'].values():
        pads=[graph['nodes'][i]['item'] for i in indices if isinstance(graph['nodes'][i]['item'],p.PAD) and graph['nodes'][i]['item'].GetNumber()]
        if pads:pad_groups.append((indices,pads))
    sizes=defaultdict(list)
    for _,pads in pad_groups:sizes[pads[0].GetNetname()].append(len(pads))
    # Search exposed smaller islands, including connected multi-pad returns.
    # Keep the single largest backbone out of this local span search. Equal
    # size islands and all historical single-pad inputs remain eligible.
    limits={net:sorted(counts)[-2] if len(counts)>1 else 1 for net,counts in sizes.items()}
    for indices,pads in pad_groups:
        if len(pads)>max(1,limits[pads[0].GetNetname()]):continue
        pad=pads[0];anchors=component_frontiers(graph,indices,deadline)
        for track in board.GetTracks():
            if time.monotonic()>=deadline:raise TimeoutError('Detour discovery deadline exhausted')
            uid=track.m_Uuid.AsString()
            if (track.GetClass()!='PCB_TRACK' or track.IsLocked() or uid in protected or
                    track.GetNetname()==pad.GetNetname() or track.GetLength()<1000000):continue
            segment=p.SEG(track.GetStart(),track.GetEnd())
            gap,anchor=min((distance(xy(segment.NearestPoint(vec(pos))),pos),pos) for pos in anchors)
            if gap>2500000:continue
            pad=min(pads,key=lambda d:(distance(xy(d.GetPosition()),anchor),d.GetParentFootprint().GetReference(),d.GetNumber()))
            entry={'id':uid,'net':track.GetNetname(),'target_net':pad.GetNetname(),'distance_nm':gap,
                   'length_nm':track.GetLength(),'layer':track.GetLayer(),
                   'target':[pad.GetParentFootprint().GetReference(),pad.GetNumber()],'anchor_nm':list(anchor)}
            key=(uid,pad.GetNetname())
            if key not in rows or gap<rows[key]['distance_nm']:rows[key]=entry
    return sorted(rows.values(),key=lambda row:(row['distance_nm'],-row['length_nm'],row['net'],row['target'],row['id']))


def completion_router(board,deadline):
    router=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=board.GetDesignSettings().m_TrackMinWidth)
    router.grid_step=125000;router.heuristic_weight=5
    router.connection_budget_s=22;router.search_budget_s=22
    router.via_cost_nm=1000000;router.layer_change_heuristic_nm=0
    return router


def repair_candidate(board,strategy,*,protected,plane_nets,request,deadline,exploratory=False):
    if type(exploratory) is not bool:raise ValueError('Exploratory repair mode must be boolean')
    from smilepcb.keyboard_native import pad_gaps
    from smilepcb.routing_style import apply_style
    from .recovery_steps import short_spokes,shift_blocking_vias
    start=time.monotonic();stop=min(deadline,start+82)
    attempt_counts=dict(request.get('attempt_counts',{}))
    before=snapshot(board);old=pad_gaps(board);old_groups=pad_partitions(board,stop)
    if not set(protected)<=before['tracks'].keys():raise ValueError('Missing protected source copper')
    negotiation=strategy in ('plane_negotiate','signal_negotiate')
    if strategy not in ('route','spokes','detour','via_shift','plane_negotiate','signal_negotiate'):raise ValueError('Unknown completion strategy')
    zones=[z.Duplicate(False) for z in board.Zones() if not z.GetIsRuleArea()]
    removed=set();details={};added=[];target=set(old)-set(plane_nets)
    if negotiation:
        from .native_graph import negotiate_routes
        selected=set(old)&set(plane_nets) if strategy=='plane_negotiate' else target
        required=None
        if 'pad_pair' in request:
            pair=request['pad_pair']
            if strategy!='plane_negotiate' or not isinstance(pair,list) or len(pair)!=2:
                raise ValueError('A physical pad pair requires plane negotiation')
            pads=[]
            for item in pair:
                if not isinstance(item,(list,tuple)) or len(item)!=2:raise ValueError('Invalid physical pad pair')
                matches=[d for f in board.GetFootprints() if f.GetReference()==item[0] for d in f.Pads() if d.GetNumber()==item[1]]
                if len(matches)!=1:raise ValueError('Physical pad pair must identify unique native pads')
                pads.append(matches[0])
            selected_nets={d.GetNetname() for d in pads}
            if len(selected_nets)!=1 or not selected_nets<=selected:raise ValueError('Physical pad pair must share an open plane net')
            selected=selected_nets;required=[d.m_Uuid.AsString() for d in pads]
        if not selected or (strategy=='plane_negotiate' and not zones):
            return {'accepted':False,'pad_group_gaps':sum(old.values()),'reason':'No disconnected groups for this strategy'}
        # Keep real filled contacts while planning the return connection.
        # Removing fills first invents hundreds of unrelated open returns.
        # Signal paths instead need the refillable pours removed before
        # planning; a foreign pour is not immutable signal-routing copper.
        if strategy=='signal_negotiate':
            for z in list(board.Zones()):
                if not z.GetIsRuleArea():board.Delete(z)
        def factory(candidate,**kw):
            router=LocalRouter(candidate,
                track_width_nm=candidate.GetDesignSettings().m_TrackMinWidth or p.FromMM(.2),**kw)
            if strategy=='signal_negotiate':
                # A last crowded terminal is not an invitation to drop the
                # displacement cost and cut every bus along a straight path.
                router.connection_budget_s=18;router.search_budget_s=18;router.allow_penalty_fallback=False
                router.displacement_cost_nm=p.FromMM(1)
                if router.quality.name=='turns':
                    router.displacement_cost_nm=p.FromMM(1 if exploratory else 5)
                    router.heuristic_weight=2
                router.via_cost_nm=1000000;router.layer_change_heuristic_nm=0
            return router
        plan=negotiate_routes(board,selected,protected,min(stop-25,start+25),router_factory=factory,required_pad_ids=required,attempt_counts=attempt_counts)
        details['plane_negotiation' if strategy=='plane_negotiate' else 'signal_negotiation']=plan;removed.update(plan['removed'])
        added+=plan['added']+plan['retained_fragments']
        target={before['tracks'][uid]['net'] for uid in removed}-set(plane_nets)
    for z in list(board.Zones()):
        if not z.GetIsRuleArea():board.Delete(z)
    if strategy=='spokes':
        details=short_spokes(board,protected,min(stop-45,start+25));removed.update(details['removed_ids'])
    elif strategy=='detour':
        from .span_detour import proposals,apply
        candidate=request['candidate'];target={candidate['target_net']}
        if not target<=set(old):raise ValueError('Detour target must have a current physical gap')
        track=next((t for t in board.GetTracks() if t.m_Uuid.AsString()==candidate['id']),None)
        if track is None:raise ValueError('Stale detour candidate')
        rows=proposals(board,track,protected=protected,deadline=min(stop-45,start+8));variant=request['variant']
        if type(variant) is not int or not 0<=variant<16:raise ValueError('Invalid bounded detour variant')
        if variant>=len(rows):return {'accepted':False,'pad_group_gaps':sum(old.values()),'reason':'No legal detour variant'}
        selected=rows[variant];added=apply(board,selected);removed.add(selected['removed_id']);details={'move':selected,'variants':len(rows)}
    elif strategy=='via_shift':
        net=request['net']
        if net not in target:raise ValueError('Via-shift target must be an open signal')
        details=shift_blocking_vias(board,net,protected,set(request.get('hard_ids',())),min(stop-25,start+50))
        if not details['planned_connections'] or details['failed_vias']:
            return {**details,'accepted':False,'pad_group_gaps':sum(old.values())}
        removed.update(move['uuid'] for move in details['moves']);added+=details['added_ids'];target=set()
    router=completion_router(board,min(stop-(12 if negotiation else 25),start+52))
    router.attempt_counts=attempt_counts
    joined=route_components(board,router,target) if target else 0
    added+=router.added
    # Only new copper is styled. Retained original conductors stay exact.
    if added:details['style']=apply_style(board,editable_ids=added)
    for z in zones:board.Add(z)
    if zones and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Completion refill failed')
    after=pad_gaps(board)
    if set(after)&set(plane_nets):
        heal=completion_router(board,min(stop-12,start+67));heal.connection_budget_s=5;heal.search_budget_s=5
        joined+=stitch_zones(board,heal,set(after)&set(plane_nets))
        if zones and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Return refill failed')
        after=pad_gaps(board)
    result={**details,'accepted':False,'before':old,'after':after,'connections':joined,
            'pad_group_gaps':sum(old.values()),'graph':{**getattr(router,'graph_diagnostic',{}),'attempt_counts':dict(attempt_counts)},'removed_source_ids':sorted(removed)}
    if sum(after.values())<sum(old.values()):
        current=snapshot(board)
        if (before['footprints']!=current['footprints'] or any(current['tracks'].get(uid)!=shape
                for uid,shape in before['tracks'].items() if uid not in removed)):
            raise ValueError('Completion changed immutable copper or components')
        preserved=partitions_preserved(old_groups,pad_partitions(board,stop))
        result['old_pad_partitions_preserved']=preserved
        result['via_land_checks']=require_via_land_clearance(board,deadline=stop)
        if preserved:result.update(accepted=True,pad_group_gaps=sum(after.values()),pad_groups_missing=after)
    if time.monotonic()>=stop:raise TimeoutError('Completion acceptance deadline exhausted')
    result['elapsed_s']=time.monotonic()-start
    return result
