"""Short, preserved plane access for single-layer controller return pads."""
import math
import time
from smilepcb.native_worker import p
from .local_router import LocalRouter,xy,vec


def seed_controller_returns(board,*,controller_refs,plane_nets,deadline,
                            via_to_smd_clearance_nm=0):
    """Reserve one real via for each controller return island before buses.

    Already reachable through-hole copper is reused. A rejected batch leaves
    all original copper and component positions exact; no partly reserved
    controller block is offered to the long-range router.
    """
    from .native_graph import copper_graph
    from .safety import require_via_land_clearance
    from smilepcb.native_worker import snapshot
    before=snapshot(board);original=set(before['tracks']);ports=[]
    access_context={}
    def require_budget():
        if time.monotonic()>=deadline:
            raise TimeoutError('Controller return access budget exhausted')
    def rollback():
        for item in list(board.GetTracks()):
            if item.m_Uuid.AsString() not in original:board.Delete(item)
    try:
        graph=copper_graph(board,set(plane_nets),deadline)
        for indices in graph['groups'].values():
            require_budget()
            nodes=[graph['nodes'][i]['item'] for i in indices if not graph['nodes'][i]['zone']]
            pads=[item for item in nodes if isinstance(item,p.PAD) and item.GetNumber()
                  and item.GetParentFootprint().GetReference() in controller_refs]
            if not pads:continue
            if any(isinstance(item,p.PCB_VIA) or (isinstance(item,p.PAD) and
                    item.IsOnLayer(p.F_Cu) and item.IsOnLayer(p.B_Cu)) for item in nodes):continue
            selected=None
            for pad in sorted(pads,key=lambda d:(d.GetParentFootprint().GetReference(),d.GetNumber())):
                trial=_seed_plane_access(board,[pad],context=access_context,deadline=deadline,
                    track_width_nm=board.GetDesignSettings().m_TrackMinWidth or p.FromMM(.2),
                    max_distance_nm=4500000,via_to_smd_clearance_nm=via_to_smd_clearance_nm)
                require_budget()
                if trial and trial[0]['connected']:
                    selected=trial[0];break
            if selected is None:
                selected=_join_existing_return(board,pads,deadline=deadline,
                    max_distance_nm=4500000,via_to_smd_clearance_nm=via_to_smd_clearance_nm)
                require_budget()
                if selected:
                    # The grid fallback owns a separate transactional router.
                    # Its accepted additions must become obstacles for later leads.
                    additions=set(selected['ids'])
                    router,_=access_context['geometry']
                    for item in board.GetTracks():
                        if item.m_Uuid.AsString() in additions:router.index(item)
            if selected is None:
                rollback()
                return {'accepted':False,'ports':ports,'added_ids':[],
                        'reason':'No legal controller return interface',
                        'failed_pad_ids':[d.m_Uuid.AsString() for d in pads]}
            selected['pad_ids']=[d.m_Uuid.AsString() for d in pads];ports.append(selected)
        require_via_land_clearance(board,deadline=deadline)
        after=snapshot(board)
        if before['footprints']!=after['footprints'] or any(after['tracks'].get(uid)!=shape
                for uid,shape in before['tracks'].items()):
            raise ValueError('Controller return access changed original geometry')
        require_budget()
        return {'accepted':True,'ports':ports,'added_ids':sorted(set(after['tracks'])-original)}
    except BaseException:
        rollback();raise


def _join_existing_return(board,pads,*,deadline,max_distance_nm,via_to_smd_clearance_nm):
    """Try a bounded native grid lead to nearby, real plated return copper.

    Simple access proposals stay the fast path. This transaction handles fine
    doglegs they cannot express; failure retains no tentative copper.
    """
    from .grid_search import search_many
    from .native_graph import copper_graph
    if time.monotonic()>=deadline:return None
    pads=sorted(pads,key=lambda d:(d.GetParentFootprint().GetReference(),d.GetNumber()))
    starts=[(xy(d.GetPosition()),layer) for d in pads[:64]
            for layer in (p.F_Cu,p.B_Cu) if d.IsOnLayer(layer)]
    if not starts:return None
    net=pads[0].GetNetname()
    vias=[t for t in board.GetTracks() if isinstance(t,p.PCB_VIA) and t.GetNetname()==net
          and t.IsOnLayer(p.F_Cu) and t.IsOnLayer(p.B_Cu)
          and min(math.dist(xy(t.GetPosition()),a) for a,_ in starts)<=max_distance_nm]
    vias=sorted(vias,key=lambda t:(min(math.dist(xy(t.GetPosition()),a) for a,_ in starts),
                                  t.m_Uuid.AsString()))[:32]
    lands=[]
    for fp in board.GetFootprints():
        for d in fp.Pads():
            if time.monotonic()>=deadline:return None
            if d.GetAttribute()==p.PAD_ATTRIB_SMD:
                lands.extend(d.GetEffectiveShape(layer) for layer in (p.F_Cu,p.B_Cu) if d.IsOnLayer(layer))
    def land_clear(via):
        if time.monotonic()>=deadline:return False
        point=vec(xy(via.GetPosition()))
        hole=p.SHAPE_CIRCLE(point,via.GetDrillValue()//2)
        annulus=p.SHAPE_CIRCLE(point,max(via.GetWidth(layer) for layer in (p.F_Cu,p.B_Cu))//2)
        for shape in lands:
            if time.monotonic()>=deadline:return False
            if p.SHAPE.Collide(hole,shape,p.FromMM(.1)):return False
            if via_to_smd_clearance_nm and p.SHAPE.Collide(annulus,shape,via_to_smd_clearance_nm):return False
        return True
    vias=[via for via in vias if land_clear(via)]
    if not vias:return None
    targets=[(xy(via.GetPosition()),layer) for via in vias for layer in (p.F_Cu,p.B_Cu)]
    router=LocalRouter(board,deadline=deadline,compact=True,
        track_width_nm=board.GetDesignSettings().m_TrackMinWidth or p.FromMM(.2))
    router.grid_step=125000;router.search_budget_s=2;router.search_expansion_limit=20000
    router.heuristic_weight=5;router.via_cost_nm=1000000;router.layer_change_heuristic_nm=0
    accepted=False
    try:
        if not search_many(router,starts,targets,net):return None
        added=set(router.added)
        tracks=[t for t in board.GetTracks() if t.m_Uuid.AsString() in added]
        if sum(t.GetLength() for t in tracks if not isinstance(t,p.PCB_VIA))>max_distance_nm:return None
        if any(not land_clear(t) for t in tracks if isinstance(t,p.PCB_VIA)):return None
        graph=copper_graph(board,{net},deadline,include_zones=False)
        ids={node['item'].m_Uuid.AsString():i for i,node in enumerate(graph['nodes'])}
        roots={graph['find'](ids[d.m_Uuid.AsString()]) for d in pads}
        reached=[via for via in vias if graph['find'](ids[via.m_Uuid.AsString()]) in roots]
        if len(roots)!=1 or not reached or time.monotonic()>=deadline:return None
        accepted=True
        return {'ref':pads[0].GetParentFootprint().GetReference(),'pin':pads[0].GetNumber(),
                'net':net,'connected':True,'ids':list(router.added),
                'via_id':reached[0].m_Uuid.AsString(),'reused':True,
                'method':'native_grid_to_existing_via','search':router.last_search}
    finally:
        if not accepted:
            added=set(router.added)
            for item in list(board.GetTracks()):
                if item.m_Uuid.AsString() in added:board.Delete(item)


def connect_plane_returns(board,plane_nets,*,deadline=None,grid_step_nm=500000):
    """Join real return-pad groups using the native legal routing width."""
    from .native_graph import route_components
    width=board.GetDesignSettings().m_TrackMinWidth or p.FromMM(.2)
    router=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=width)
    router.grid_step=grid_step_nm;router.heuristic_weight=3
    router.connection_budget_s=5
    count=route_components(board,router,set(plane_nets))
    return {'connections':count,'added_ids':router.added,'graph':getattr(router,'graph_diagnostic',{})}


def seed_plane_access(board,pads,*,deadline=None,track_width_nm=None,max_distance_nm=2000000,
                      via_to_smd_clearance_nm=0):
    return _seed_plane_access(board,pads,context={},deadline=deadline,track_width_nm=track_width_nm,
                              max_distance_nm=max_distance_nm,via_to_smd_clearance_nm=via_to_smd_clearance_nm)


def _seed_plane_access(board,pads,*,context,deadline,track_width_nm,max_distance_nm,
                       via_to_smd_clearance_nm):
    if type(max_distance_nm) is not int or not 800000<=max_distance_nm<=10000000:
        raise ValueError('Escape length must be between 0.8 and 10 mm')
    if type(via_to_smd_clearance_nm) is not int or not 0<=via_to_smd_clearance_nm<=2000000:
        raise ValueError('Via-to-SMD clearance must be between 0 and 2 mm')
    if deadline is not None and time.monotonic()>=deadline:return []
    pads=list(pads)
    if not pads:return []
    # Access must already be connected by explicit copper. A filled plane can
    # become stale as later routes are added and is not a local escape path.
    from .native_graph import copper_graph
    graph=copper_graph(board,{pad.GetNetname() for pad in pads},deadline,include_zones=False)
    roots={node['item'].m_Uuid.AsString():graph['find'](i) for i,node in enumerate(graph['nodes'])}
    access={}
    for root,indices in graph['groups'].items():
        access[root]=[graph['nodes'][i]['item'] for i in indices
                      if isinstance(graph['nodes'][i]['item'],p.PCB_VIA)]
    if 'geometry' not in context:
        router=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=track_width_nm)
        lands={}
        for f in board.GetFootprints():
            for pad in f.Pads():
                if router.expired():return []
                if pad.GetAttribute()!=p.PAD_ATTRIB_SMD:continue
                for layer in (p.F_Cu,p.B_Cu):
                    if not pad.IsOnLayer(layer):continue
                    shape=pad.GetEffectiveShape(layer)
                    for cell in router.cells(shape.BBox(),max(p.FromMM(.1),via_to_smd_clearance_nm)):lands.setdefault(cell,[]).append(shape)
        context['geometry']=router,lands
    router,lands=context['geometry']
    report=[]
    radii=sorted(set(range(800000,max_distance_nm+1,200000))|{max_distance_nm})
    def lands_clear(hole,clearance):
        seen=set()
        for cell in router.cells(hole.BBox()):
            if router.expired():return False
            for shape in lands.get(cell,()):
                if id(shape) in seen:continue
                seen.add(id(shape))
                if p.SHAPE.Collide(hole,shape,clearance):return False
        return True
    for pad in pads:
        if router.expired():break
        layers=[layer for layer in (p.F_Cu,p.B_Cu) if pad.IsOnLayer(layer)]
        if len(layers)!=1:continue
        layer=layers[0];start=xy(pad.GetPosition());net=pad.GetNetname()
        row={'ref':pad.GetParentFootprint().GetReference(),'pin':pad.GetNumber(),
             'net':net,'connected':False,'ids':[]}
        root=roots[pad.m_Uuid.AsString()]
        for existing in sorted(access.get(root,()),key=lambda via:(
                math.dist(start,xy(via.GetPosition())),via.m_Uuid.AsString())):
            end=xy(existing.GetPosition())
            if math.dist(start,end)>max_distance_nm:continue
            if not lands_clear(p.SHAPE_CIRCLE(vec(end),existing.GetDrillValue()//2),p.FromMM(.1)):continue
            if via_to_smd_clearance_nm and not lands_clear(
                    p.SHAPE_CIRCLE(vec(end),existing.GetWidth(layer)//2),via_to_smd_clearance_nm):continue
            row.update(connected=True,via_id=existing.m_Uuid.AsString(),reused=True)
            break
        if row['connected']:
            report.append(row);continue
        # Preserve simple access before considering more congested doglegs.
        for allow_bend in (False,True):
            for radius in radii:
                if router.expired():break
                for degrees in range(0,360,45):
                    if router.expired():break
                    angle=math.radians(degrees)
                    end=(start[0]+round(radius*math.cos(angle)),start[1]+round(radius*math.sin(angle)))
                    # A connected annulus may touch its pad; the drill may not
                    # puncture that solder land or sit directly at its edge.
                    hole=p.SHAPE_CIRCLE(vec(end),router.drill//2)
                    if not lands_clear(hole,p.FromMM(.1)):continue
                    # Some exchange formats cannot express the native distinction
                    # between a same-net annulus and its smaller physical drill.
                    # Keep the real via intact and select a more distant legal
                    # position rather than shrinking its router representation.
                    if via_to_smd_clearance_nm and not lands_clear(
                            p.SHAPE_CIRCLE(vec(end),router.via_width//2),via_to_smd_clearance_nm):continue
                    if not router.via_clear(end,net):continue
                    shape=p.SHAPE_SEGMENT(vec(start),vec(end),router.track_width)
                    path=[start,end]
                    if not router.clear(shape,layer,net):
                        if not allow_bend:continue
                        path=router.leg(start,end,layer,net)
                        if path is None or sum(math.dist(a,z) for a,z in zip(path,path[1:]))>max_distance_nm:continue
                    if router.expired():break
                    mark=len(router.added);router.add_path(path,layer,net);router.add_via(end,net)
                    row.update(connected=True,ids=router.added[mark:],via_id=router.added[-1],reused=False)
                    # The new via belongs to this original pad group; subsequent
                    # pads in the same batch should not overlay a second escape.
                    access.setdefault(root,[]).append(next(item for item in board.GetTracks()
                        if item.m_Uuid.AsString()==row['via_id']))
                    break
                if row['connected']:break
            if row['connected']:break
        report.append(row)
    return report


def connect_controller_return_tree(board,cfg,*,deadline=None,budget_s=75):
    """Join every actual controller return pad, without requiring plane fills.

    Both routing passes and the independent final grouping/solder-land checks
    share one deadline. Incomplete or exceptional batches retain no additions.
    """
    from smilepcb.native_worker import snapshot
    from .native_graph import copper_graph,route_components
    from .safety import require_via_land_clearance
    if type(budget_s) not in (int,float) or not 0<budget_s<=75:
        raise ValueError('Controller return tree budget must be positive and at most 75 seconds')
    started=time.monotonic();stop=started+budget_s
    if deadline is not None:stop=min(stop,deadline)
    before=snapshot(board);original=set(before['tracks'])
    refs={part['ref'] for part in cfg['parts'] if part['region']=='controller' and not part['ref'].startswith('#')}
    planes=set(cfg['plane_nets'])
    pads=[d for fp in board.GetFootprints() if fp.GetReference() in refs for d in fp.Pads()
          if d.GetNumber() and d.GetNetname() in planes]
    required=[d.m_Uuid.AsString() for d in pads];nets={d.GetNetname() for d in pads}
    report={'accepted':False,'connections':0,'missing':{},'missing_checked':False,'history':[],
            'required':len(pads),'required_pad_ids':required,'added_ids':[]}
    try:
        reserve=min(5,max(0,stop-started)/10)
        for grid in (500000,125000):
            phase_stop=min(stop-reserve,time.monotonic()+32)
            if time.monotonic()>=phase_stop:break
            router=LocalRouter(board,deadline=phase_stop,compact=True,
                track_width_nm=board.GetDesignSettings().m_TrackMinWidth or p.FromMM(.2))
            router.grid_step=grid;router.connection_budget_s=2;router.search_budget_s=2
            router.via_cost_nm=1000000;router.layer_change_heuristic_nm=0;router.heuristic_weight=5
            row={'grid':grid,'connections':0}
            report['history'].append(row)
            try:
                row['connections']=route_components(board,router,nets,required_pad_ids=required)
            except TimeoutError as error:
                row['reason']=str(error)
            row['diagnostic']=getattr(router,'graph_diagnostic',{})
        graph=copper_graph(board,nets,stop)
        ids={node['item'].m_Uuid.AsString():i for i,node in enumerate(graph['nodes'])}
        groups={net:set() for net in nets}
        for pad in pads:
            groups[pad.GetNetname()].add(graph['find'](ids[pad.m_Uuid.AsString()]))
        report['missing']={net:len(rows)-1 for net,rows in groups.items() if len(rows)>1}
        report['missing_checked']=True
        if report['missing']:
            report['reason']='Controller return pads remain physically disconnected'
            return report
        require_via_land_clearance(board,deadline=stop)
        if time.monotonic()>=stop:raise TimeoutError('Controller return tree budget exhausted')
        after=snapshot(board)
        if after['footprints']!=before['footprints'] or any(after['tracks'].get(uid)!=shape
                for uid,shape in before['tracks'].items()):
            raise ValueError('Controller return tree changed original geometry')
        report.update(accepted=True,connections=sum(row['connections'] for row in report['history']),
                      added_ids=sorted(set(after['tracks'])-original),source_copper_unchanged=True)
    except TimeoutError as error:
        report['reason']=str(error)
    finally:
        if not report['accepted']:
            for item in list(board.GetTracks()):
                if item.m_Uuid.AsString() not in original:board.Delete(item)
            report['added_ids']=[];report['connections']=0
        report['elapsed_s']=time.monotonic()-started
    return report
