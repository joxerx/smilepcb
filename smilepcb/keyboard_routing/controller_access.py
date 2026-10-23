"""Prove controller connections on candidate copper before accepting a pose.

Free space around an MCU, or an unattached escape via, does not prove that
its signals can reach their destinations. Retain the actual checked routes
on success and remove all candidate additions on rejection.
"""
import time
import math
from collections import defaultdict
from smilepcb.native_worker import p
from .local_router import LocalRouter
from .native_graph import copper_graph,route_components
from .local_router import xy,vec


def release_core_copper(board,*,protected_nets=()):
    """Release copied local wiring for a fresh build, keeping protocol copper.

    Footprint positions and pad connectivity are unchanged. The destination
    still needs complete routing and native acceptance before it can be used.
    """
    removed=[]
    for item in list(board.GetTracks()):
        if item.GetNetname() not in protected_nets:
            removed.append(item.m_Uuid.AsString());board.Delete(item)
    return {'removed_ids':removed}


def prune_single_terminal_stubs(board,*,protected_nets=()):
    """Adapt an extracted block, retaining copper joining two or more pads.

    Run on the extracted source copy before assembling the destination board.
    Single-terminal external tails have no internal connection to preserve.
    Callers must explicitly protect protocol copper they intend to extend.
    """
    terminals=defaultdict(set)
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNumber() and pad.GetNetname():
                terminals[pad.GetNetname()].add(pad.m_Uuid.AsString())
    removed=[]
    for item in list(board.GetTracks()):
        net=item.GetNetname()
        if net not in protected_nets and len(terminals[net])==1:
            removed.append(item.m_Uuid.AsString());board.Delete(item)
    return {'removed_ids':removed}


def _requirements(board,refs):
    local=defaultdict(set);external=defaultdict(set)
    for fp in board.GetFootprints():
        target=local if fp.GetReference() in refs else external
        for pad in fp.Pads():
            if pad.GetNumber() and pad.GetNetname():
                target[pad.GetNetname()].add(pad.m_Uuid.AsString())
    return {net:{'local':ids,'external':external[net]} for net,ids in local.items()
            if len(ids)>1 or external[net]}


def _missing(board,requirements,deadline):
    graph=copper_graph(board,set(requirements),deadline)
    ids={node['item'].m_Uuid.AsString():i for i,node in enumerate(graph['nodes'])
         if isinstance(node['item'],p.PAD)}
    missing=[]
    for net,req in requirements.items():
        roots={graph['find'](ids[uid]) for uid in req['local']}
        if len(roots)!=1 or (req['external'] and not any(
                graph['find'](ids[uid]) in roots for uid in req['external'])):
            missing.append(net)
    return sorted(missing)


def _fill_planes(board,nets):
    box=board.GetBoardEdgesBoundingBox()
    for net in sorted(nets):
        for layer in (p.F_Cu,p.B_Cu):
            zone=p.ZONE(board);zone.SetLayer(layer);zone.SetNet(board.FindNet(net))
            zone.SetLocalClearance(p.FromMM(.2));zone.SetMinThickness(p.FromMM(.2))
            zone.SetPadConnection(p.ZONE_CONNECTION_THT_THERMAL)
            zone.SetThermalReliefGap(p.FromMM(.25));zone.SetThermalReliefSpokeWidth(p.FromMM(.3))
            zone.SetIslandRemovalMode(p.ISLAND_REMOVAL_MODE_ALWAYS)
            shape=zone.Outline();shape.NewOutline()
            for x,y in ((box.GetLeft(),box.GetTop()),(box.GetRight(),box.GetTop()),
                        (box.GetRight(),box.GetBottom()),(box.GetLeft(),box.GetBottom())):
                shape.Append(x,y)
            board.Add(zone)
    board.BuildConnectivity()
    if nets and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Controller plane refill failed')


def route_controller_access(board,*,controller_refs,power_nets=(),usb_nets=(),plane_nets=(),deadline):
    if any(not z.GetIsRuleArea() for z in board.Zones()):
        raise ValueError('Controller placement access requires a fresh candidate without existing copper zones')
    started=time.monotonic()
    refs=set(controller_refs);present={f.GetReference() for f in board.GetFootprints()}
    if not refs or not refs<=present:raise ValueError('Controller access requires existing controller references')
    if not time.monotonic()<deadline:raise ValueError('Controller access requires a positive time budget')
    requirements=_requirements(board,refs);before={t.m_Uuid.AsString() for t in board.GetTracks()}
    before_zones={z.m_Uuid.AsString() for z in board.Zones()}
    result={'accepted':False,'missing':sorted(requirements),'required_nets':len(requirements),'attempts':[],'timing':[]}
    def timing(stage):result['timing'].append({'stage':stage,'elapsed_s':time.monotonic()-started,'remaining_s':deadline-time.monotonic()})
    try:
        routing_deadline=deadline-min(30,(deadline-time.monotonic())/3)
        router=LocalRouter(board,deadline=routing_deadline,compact=True,
                           track_width_nm=max(p.FromMM(.15),board.GetDesignSettings().m_TrackMinWidth))
        router.grid_step=250000;router.heuristic_weight=5
        router.via_cost_nm=1000000;router.layer_change_heuristic_nm=0
        router.connection_budget_s=min(2,max(.1,(routing_deadline-time.monotonic())/max(1,2*len(requirements))))
        router.attempt_counts={}
        # Route protocols without vias. Internal controller connections use
        # a local pad subset; shared matrix/LED power loads are left for the
        # subsequent global routing stage.
        boundary={net for net,req in requirements.items() if req['external']}
        protocol=set(usb_nets)&requirements.keys()
        counts={f.GetReference():sum(bool(d.GetNumber()) for d in f.Pads())
                for f in board.GetFootprints() if f.GetReference() in refs}
        ports={ref for ref,count in counts.items() if count==max(counts.values())}
        # Reserve physical layer access before long routes choose via landings
        # in front of adjacent fine-pitch pads. The most constrained pads get
        # first choice; all choices still use the native collision oracle.
        pads=[d for f in board.GetFootprints() if f.GetReference() in refs for d in f.Pads()
              if f.GetReference() in ports or d.GetNetname() in plane_nets
              if d.GetNumber() and d.GetNetname() in requirements and d.GetNetname() not in protocol]
        def options(pad):
            origin=xy(pad.GetPosition());layer=pad.GetParentFootprint().GetLayer();score=0
            for radius in [*range(800000,4500001,200000),4500000]:
                for degrees in range(0,360,45):
                    if router.expired():return score
                    angle=math.radians(degrees)
                    end=(origin[0]+round(radius*math.cos(angle)),origin[1]+round(radius*math.sin(angle)))
                    if router.via_clear(end,pad.GetNetname()) and router.clear(
                            p.SHAPE_SEGMENT(vec(origin),vec(end),router.track_width),layer,pad.GetNetname()):score+=1
            return score
        pads.sort(key=lambda d:(options(d),d.GetNetname(),d.GetParentFootprint().GetReference(),d.GetNumber()))
        from .power import seed_plane_access
        result['early_layer_access']=seed_plane_access(board,pads,deadline=min(routing_deadline,time.monotonic()+10),
                track_width_nm=router.track_width,max_distance_nm=p.FromMM(4.5))
        timing('early layer access')
        router=LocalRouter(board,deadline=routing_deadline,compact=True,track_width_nm=router.track_width)
        router.grid_step=250000;router.heuristic_weight=5;router.via_cost_nm=1000000;router.layer_change_heuristic_nm=0
        router.connection_budget_s=min(2,max(.1,(routing_deadline-time.monotonic())/max(1,2*len(requirements))))
        router.attempt_counts={}
        active=requirements.keys()-set(plane_nets)
        phases=((protocol,True,True),
                (active-protocol,True,False),
                (active-protocol,False,True),
                (boundary-protocol-set(power_nets)-set(plane_nets),False,True),
                ((boundary-protocol) & (set(power_nets)-set(plane_nets)),False,True))
        for phase,(selected,local,external) in enumerate(phases):
            if not selected or router.expired():continue
            original_via_clear=router.via_clear
            original_width=router.track_width
            if selected<=set(usb_nets):
                router.via_clear=lambda position,net:False
                router.track_width=max(p.FromMM(.2),original_width)
            try:
                if local:
                    local_ids=set().union(*(requirements[n]['local'] for n in selected))
                    route_components(board,router,selected,required_pad_ids=local_ids)
                    result['attempts'].extend(router.graph_diagnostic.get('attempts',[]))
                if external and not router.expired():
                    route_components(board,router,selected,interface_refs=ports if phase==2 else refs)
                    result['attempts'].extend(router.graph_diagnostic.get('attempts',[]))
            except TimeoutError as error:
                result.setdefault('planning_timeouts',[]).append({'stage':'coarse '+str(phase),'error':str(error)})
            finally:
                router.via_clear=original_via_clear;router.track_width=original_width
            timing('coarse phase '+str(phase))
        pending=set(_missing(board,requirements,deadline))-set(plane_nets)
        result['missing_before_fine_pass']=sorted(pending)
        recover=pending & boundary-set(usb_nets)
        if recover and deadline-time.monotonic()>8:
            from .native_graph import negotiate_routes
            rescue_stop=deadline-min(18,(deadline-time.monotonic())*.6)
            def rescue_router(candidate,**kw):
                r=LocalRouter(candidate,track_width_nm=router.track_width,**kw)
                r.connection_budget_s=1.5;r.via_cost_nm=1000000;r.layer_change_heuristic_nm=0
                return r
            try:
                protected=before|{t.m_Uuid.AsString() for t in board.GetTracks() if t.GetNetname() in protocol}
                result['early_recovery']=negotiate_routes(board,recover,protected,rescue_stop,
                        router_factory=rescue_router,interface_refs=refs)
            except TimeoutError as error:
                result.setdefault('planning_timeouts',[]).append({'stage':'candidate recovery','error':str(error)})
            pending=set(_missing(board,requirements,deadline))-set(plane_nets)
            timing('candidate recovery')
        if pending and deadline-time.monotonic()>.5:
            fine_stop=deadline-min(8,(deadline-time.monotonic())/2)
            fine=LocalRouter(board,deadline=fine_stop,compact=True,track_width_nm=router.track_width)
            fine.grid_step=125000;fine.heuristic_weight=5;fine.via_cost_nm=1000000;fine.layer_change_heuristic_nm=0
            fine.connection_budget_s=min(3,max(.1,(fine_stop-time.monotonic())/max(1,2*len(pending))))
            for selected in (pending & set(usb_nets),pending-set(usb_nets)):
                if not selected or fine.expired():continue
                old_width=fine.track_width;old_via_clear=fine.via_clear
                if selected<=set(usb_nets):
                    fine.track_width=max(p.FromMM(.2),old_width);fine.via_clear=lambda position,net:False
                try:
                    local_ids=set().union(*(requirements[n]['local'] for n in selected))
                    route_components(board,fine,selected,required_pad_ids=local_ids)
                    result['attempts'].extend(fine.graph_diagnostic.get('attempts',[]))
                    if selected & boundary and not fine.expired():
                        route_components(board,fine,selected & boundary,interface_refs=refs)
                        result['attempts'].extend(fine.graph_diagnostic.get('attempts',[]))
                except TimeoutError as error:
                    result.setdefault('planning_timeouts',[]).append({'stage':'fine','error':str(error)})
                finally:fine.track_width=old_width;fine.via_clear=old_via_clear
            timing('fine phase')
        actual_planes=set(plane_nets)&requirements.keys()
        _fill_planes(board,actual_planes)
        timing('plane fill')
        if actual_planes:
            from .native_graph import stitch_zones
            stitch=LocalRouter(board,deadline=deadline-min(5,(deadline-time.monotonic())* .6),compact=True)
            result['plane_stitches']=stitch_zones(board,stitch,actual_planes)
            if not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Controller stitching refill failed')
            timing('plane stitching and refill')
        result['missing']=_missing(board,requirements,deadline)
        timing('final connectivity proof')
        result['accepted']=not result['missing']
    except TimeoutError as error:
        result['budget_exhausted']=str(error)
    finally:
        added=[t for t in board.GetTracks() if t.m_Uuid.AsString() not in before]
        result['added_objects']=len(added)
        if not result['accepted']:
            for zone in list(board.Zones()):
                if zone.m_Uuid.AsString() not in before_zones:board.Delete(zone)
            for item in added:board.Delete(item)
    return result
