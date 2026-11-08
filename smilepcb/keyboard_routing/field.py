"""Monotone native copper paths with inline, clearance-checked layer bridges."""
import math
import time
import statistics
from collections import Counter
from smilepcb.native_worker import p
from smilepcb.routing_style import octile_paths
from .local_router import LocalRouter, vec, xy


def _blocked_straight_projection(router, shape, net):
    """Prove a layer-independent material or drill barrier, otherwise defer.

    Switching faces cannot avoid these exact native collisions. Ordinary
    copper, including a hole owner's annulus, remains the axial planner's
    responsibility; a clear drill is not inferred from its copper UUID.
    """
    box=shape.BBox()
    center=p.VECTOR2I((box.GetLeft()+box.GetRight())//2,(box.GetTop()+box.GetBottom())//2)
    if not router.outline.Contains(center):return True
    seen_edges=set()
    for cell in router.cells(box):
        for edge in router.edge_buckets.get(cell,()):
            if id(edge) in seen_edges:continue
            seen_edges.add(id(edge))
            if p.SHAPE.Collide(shape,edge,router.edge_clearance):return True
    seen_holes=set()
    padding=max(router.net_clearances.get(net,0),router.generation.foreign_copper_clearance_nm)
    for cell in router.cells(box,padding):
        for uid,hole,othernet in router.holes.get(cell,()):
            if uid in seen_holes or othernet==net:continue
            seen_holes.add(uid)
            if p.SHAPE.Collide(shape,hole,router.hole_clearances[uid]):return True
    return False


def axial_path(router, a, b, layer, net, *, step_nm=250000):
    """Plan a straight projection, changing only its copper face at obstacles.

    Exact native shapes govern both track and drill clearance. No candidate
    copper is committed here. A hole or cutout blocking both faces rejects
    the lane rather than introducing a hidden sideways displacement.
    """
    a,b = tuple(a),tuple(b)
    dx,dy=b[0]-a[0],b[1]-a[1]
    if dx and dy and abs(dx)!=abs(dy):
        raise ValueError('Straight matrix legs must be axis aligned or 45 degrees')
    if not dx and not dy: return {'paths':[], 'vias':[], 'cost':0}
    other=p.B_Cu if layer==p.F_Cu else p.F_Cu
    whole=p.SHAPE_SEGMENT(vec(a),vec(b),router.track_width)
    if router.clear(whole,layer,net): return {'paths':[([a,b],layer)],'vias':[],'cost':math.dist(a,b)}
    if _blocked_straight_projection(router,whole,net):return None
    count=math.ceil(max(abs(dx),abs(dy))/step_nm)
    points=[(a[0]+round(dx*i/count),a[1]+round(dy*i/count)) for i in range(count+1)]
    faces=(layer,other);cost={(0,0):0};previous={};via_cache={}
    for i,point in enumerate(points):
        if router.expired(): return None
        live=[j for j in (0,1) if (i,j) in cost]
        if not live: return None
        # One positive-cost switch is sufficient at a single position.
        if i not in via_cache: via_cache[i]=router.via_clear(point,net)
        if via_cache[i]:
            for j in sorted(live,key=lambda j:cost[i,j]):
                value=cost[i,j]+1000000
                if value<cost.get((i,1-j),math.inf):
                    cost[i,1-j]=value;previous[i,1-j]=(i,j)
        if i==count: break
        shape=p.SHAPE_SEGMENT(vec(point),vec(points[i+1]),router.track_width)
        for j in (0,1):
            if (i,j) in cost and router.clear(shape,faces[j],net):
                cost[i+1,j]=cost[i,j]+math.dist(point,points[i+1])*(1 if j==0 else 6)
                previous[i+1,j]=(i,j)
    key=(count,0)
    if key not in cost: return None
    result_cost=cost[key];states=[key]
    while key!=(0,0):
        key=previous[key];states.append(key)
    states.reverse();paths=[];vias=[];start=points[0];face=layer
    for old,new in zip(states,states[1:]):
        if old[1]!=new[1]:
            point=points[old[0]]
            if start!=point: paths.append(([start,point],face))
            vias.append(point);start=point;face=faces[new[1]]
    if start!=b: paths.append(([start,b],face))
    from .grid_search import valid_plan,planned_vias_clear
    spacing=max(p.FromMM(.61),router.drill+p.FromMM(.26))
    while True:
        bad=next((i for i,(x,z) in enumerate(zip(vias,vias[1:])) if
            math.dist(x,z)<spacing or not planned_vias_clear(router,x,z)),None)
        if bad is None:break
        # Merge adjacent opposite-face bridges instead of placing two vias
        # too close in the preferred-face gap between them.
        if bad%2==0:return None
        proposed=vias[:bad]+vias[bad+2:]
        candidate=[];start=a;face=layer
        for point in [*proposed,b]:
            if start!=point:candidate.append(([start,point],face))
            start=point;face=other if face==layer else layer
        if not all(router.clear(p.SHAPE_SEGMENT(vec(x),vec(z),router.track_width),side,net)
            for path,side in candidate for x,z in zip(path,path[1:])):return None
        vias=proposed;paths=candidate
    if not valid_plan(router,[(side,path) for path,side in paths],vias,net):return None
    return {'paths':paths,'vias':vias,'cost':sum(math.dist(x,z)*(1 if side==layer else 6)
        for path,side in paths for x,z in zip(path,path[1:]))+1000000*len(vias)}


def commit_plan(router, plan, net):
    from .grid_search import valid_plan
    if not valid_plan(router,[(side,path) for path,side in plan['paths']],plan['vias'],net):
        raise ValueError('Matrix plan has incompatible copper or vias')
    for path,layer in plan['paths']: router.add_path(path,layer,net)
    for point in plan['vias']: router.add_via(point,net)


def _seed_straight_row(board, net, pads, *, row_layer, domain, deadline):
    """Try the original whole horizontal spine and its local pad branches."""
    width=board.GetDesignSettings().m_TrackMinWidth or 150000
    report={'rows':[]}
    def rollback(keep):
        for item in list(board.GetTracks()):
            if item.m_Uuid.AsString() not in keep:board.Delete(item)
    keep={t.m_Uuid.AsString() for t in board.GetTracks()}
    router=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=width)
    points=sorted({xy(d.GetPosition()) for d in pads});counts=Counter(pt[1] for pt in points)
    center=sorted(counts,key=lambda y:(-counts[y],abs(y-statistics.median(q[1] for q in points)),y))[0]
    from .field_geometry import row_channels
    events=_row_obstacles(router,points[0][0],points[-1][0],row_layer,net)
    offsets=[y-center for y in row_channels(center,*domain,events)]
    selected=None
    # First find a whole preferred-face lane. Crossing the second pad of
    # every diode with two vias is worse than one nearby parallel lane.
    candidates=[]
    for offset in offsets:
        if router.expired(): break
        y=center+offset;a=(points[0][0],y);b=(points[-1][0],y)
        branches=[];missing=[];landings=[]
        for pad in pads:
            start=xy(pad.GetPosition());target=(start[0],y)
            if not pad.IsOnLayer(row_layer): missing.append((pad,target));landings.append(target);continue
            if start==target: landings.append(target);continue
            # A short diagonal enters the straight lane without a 90° stub.
            delta=abs(start[1]-y);entry=min(5*width,delta);sign_y=1 if y>start[1] else -1
            branch_paths=[[start,(start[0],y-sign_y*entry),(start[0]+sign*entry,y)] for sign in (1,-1)]
            branch=next((path for path in branch_paths if a[0]<=path[-1][0]<=b[0] and all(
                router.clear(p.SHAPE_SEGMENT(vec(u),vec(v),width),row_layer,net) for u,v in zip(path,path[1:]) if u!=v)),None)
            if branch is None: missing.append((pad,target));landings.append(target);continue
            branches.append(([q for i,q in enumerate(branch) if not i or q!=branch[i-1]],row_layer))
            landings.append(branch[-1])
        a=(min(q[0] for q in landings),y);b=(max(q[0] for q in landings),y)
        shape=p.SHAPE_SEGMENT(vec(a),vec(b),width);clear=router.clear(shape,row_layer,net)
        candidates.append((len(missing),abs(offset),y,a,b,branches,missing,clear))
        if clear and not missing:
            selected=(y,{'paths':[([a,b],row_layer)],'vias':[],'cost':math.dist(a,b)},branches,missing);break
    if selected is None:
        for _,_,y,a,b,branches,missing,clear in sorted(candidates,key=lambda row:(not row[-1],row[0],row[1])):
            if router.expired(): break
            plan=axial_path(router,a,b,row_layer,net)
            if plan is not None:
                selected=(y,plan,branches,missing);break
    if selected:
        y,plan,branches,missing=selected
        trunk_before={t.m_Uuid.AsString() for t in board.GetTracks()}
        commit_plan(router,plan,net)
        trunk_ids=sorted({t.m_Uuid.AsString() for t in board.GetTracks()}-trunk_before)
        for path,face in branches: router.add_path(path,face,net)
        from .matrix import KeyboardRouter
        from .native_graph import Endpoint
        attached=True;fallback=None
        for pad,target in missing:
            branch_deadline=min(deadline,time.monotonic()+3)
            if fallback is None:
                fallback=KeyboardRouter(board,deadline=branch_deadline,compact=True,track_width_nm=width)
                fallback.grid_step=250000;fallback.connection_budget_s=2;fallback.heuristic_weight=3
            else:fallback.deadline=branch_deadline
            # Only this router appends copper during the batch; add_path and
            # add_via index each new shape. Never reuse it after row rollback.
            fallback.__dict__.pop('last_search',None)
            fallback.__dict__.pop('last_plan',None)
            if not fallback.connect(pad,Endpoint(target,row_layer,net)): attached=False;break
        if attached:
            report['rows'].append({'net':net,'complete':True,'lane_y_nm':y,
                'inline_vias':len(plan['vias']),'routed_branches':len(missing),'pad_count':len(pads),'trunk_paths':plan['paths'],'trunk_ids':trunk_ids,'object_ids':sorted({t.m_Uuid.AsString() for t in board.GetTracks()}-keep)})
        else:
            rollback(keep);report['rows'].append({'net':net,'complete':False,'reason':'Straight lane found; obstructed branch search exhausted'})
    else:
        rollback(keep);report['rows'].append({'net':net,'complete':False,'reason':'No legal straight row lane in bounded geometry search'})
    row=report['rows'][0]
    if row['complete']:
        row.update(topology='single_spine',geometry_stage='construction',link_ids=[],
            access_ids=sorted(set(row['object_ids'])-set(row['trunk_ids'])))
    return row


def _column_event_bridge(router,a,b,layer,net):
    """Try a small event-rail detour with physical vias at both turns."""
    began=time.monotonic();old_deadline=router.deadline
    stop=min(old_deadline or float('inf'),began+3);router.deadline=stop
    diagnostic=dict(accepted=False,attempted=0,reason='no-native-admission');router.last_column_bridge=diagnostic;best=None
    try:
        from .field_geometry import column_bridge_paths
        from .layer_plan import simple_layer_plan
        from .grid_search import valid_plan
        from .bundle_polish import retained_contacts_clear
        other=p.B_Cu if layer==p.F_Cu else p.F_Cu
        margin=router.via_width//2+router.minimum_clearance;events=[]
        for face in (layer,other):
            if router.expired():return best
            for low,high in _row_obstacles(router,min(a[0],b[0]),max(a[0],b[0]),face,net):
                events.extend((low-margin,high+margin))
        if router.expired():return best
        candidates=column_bridge_paths(a,b,rail_ys=events,limit=256)
        diagnostic['proposals']=len(candidates)
        items={t.m_Uuid.AsString():t for t in router.board.GetTracks()}
        for carriers in candidates:
            if router.expired():break
            diagnostic['attempted']+=1;corners=[carriers[0][-1],carriers[1][-1]]
            if not all(router.via_clear(q,net) for q in corners) or not router.via_pair_clear(*corners):continue
            parts=[]
            for leg,face in zip(carriers,(layer,other,layer)):
                if router.expired():return best
                part=axial_path(router,*leg,face,net)
                if part is None:break
                parts.append(part)
            if len(parts)!=3:continue
            paths=[];vias=[]
            for i,part in enumerate(parts):
                paths.extend(part['paths']);vias.extend(part['vias'])
                if i<2:vias.append(corners[i])
            if router.expired():return best
            if not simple_layer_plan([(face,path) for path,face in paths],vias,
                    width=router.track_width,via_width=router.via_width):continue
            if not valid_plan(router,[(face,path) for path,face in paths],vias,net):continue
            if not all(not router.expired() and retained_contacts_clear(router.board,path,face,net,router.track_width,oracle=router,track_items=items) for path,face in paths):continue
            if router.expired():return best
            candidate=dict(paths=paths,vias=vias,cost=sum(q['cost'] for q in parts)+2000000*len(corners),topology='event-rail-corner-vias')
            if best is None or candidate['cost']<best['cost']:
                best=candidate;diagnostic.update(accepted=True,reason='native-admitted',chosen_cost=best['cost'])
        return best
    finally:
        diagnostic['elapsed_s']=time.monotonic()-began
        if not diagnostic['accepted'] and time.monotonic()>=stop:diagnostic['reason']='local-event-deadline'
        router.deadline=old_deadline


def _seed_groups(board, *, rows, columns, row_key_y, row_layer, column_layer, deadline):
    """Seed named physical pad groups; preserve every pre-existing object.

    Rows first try one global horizontal lane, with inline layer bridges.
    Finite failure may use physical straight clusters and explicit row links.
    Columns connect in physical Y order, with 45-degree stagger transitions.
    Unseeded groups are explicitly reported for the completion stage.
    """
    from smilepcb.native_worker import snapshot
    from .power import seed_plane_access
    from .field_policy import FieldPathPolicy
    from .safety import require_via_land_clearance
    original=snapshot(board);protected=[];report={'rows':[],'columns':[]}
    width=board.GetDesignSettings().m_TrackMinWidth or 150000
    def rollback(keep):
        for t in list(board.GetTracks()):
            if t.m_Uuid.AsString() not in keep: board.Delete(t)
    from copy import deepcopy
    from .interface_launches import current_launch_inventory
    from .field_geometry import row_domain
    row_centers=sorted(statistics.median(d.GetPosition().y for d in group) for group in rows.values())
    pitch=min((b-a for a,b in zip(row_centers,row_centers[1:]) if b>a),default=18000000)
    for net,pads in sorted(rows.items(),key=lambda row:statistics.median(d.GetPosition().y for d in row[1])):
        keep={t.m_Uuid.AsString() for t in board.GetTracks()};row=None
        inventory=current_launch_inventory()
        launches=None if inventory is None else (deepcopy(inventory.ports),set(inventory.resolved),set(inventory.repair_reserved))
        try:
            domain=row_domain([d.GetPosition().y for d in pads],row_key_y[net],pitch)
            row=_seed_straight_row(board,net,pads,row_layer=row_layer,domain=domain,deadline=deadline)
            if not row['complete'] and time.monotonic()<deadline:
                # The failed straight attempt has removed its copper. Its
                # speculative source retirements must not enter a new world.
                if inventory is not None:
                    inventory.ports,inventory.resolved,inventory.repair_reserved=deepcopy(launches)
                from .row_clusters import route_row_clusters
                fallback=route_row_clusters(board,net,pads,row_layer=row_layer,domain=domain,deadline=deadline)
                if fallback is not None:row=fallback
            if row['complete']:protected.extend(row['object_ids'])
            report['rows'].append(row)
        finally:
            if row is None or not row['complete']:
                rollback(keep);board.BuildConnectivity()
                if inventory is not None:inventory.ports,inventory.resolved,inventory.repair_reserved=launches
    from .matrix import KeyboardRouter
    from .native_graph import Endpoint
    column_router=None
    for net,pads in sorted(columns.items(),key=lambda row:statistics.median(d.GetPosition().x for d in row[1])):
        if time.monotonic()>=deadline:
            report['columns'].append({'net':net,'complete':False,'reason':'Budget exhausted'});continue
        keep={t.m_Uuid.AsString() for t in board.GetTracks()}
        inventory=current_launch_inventory()
        launches=None if inventory is None else (deepcopy(inventory.ports),set(inventory.resolved),set(inventory.repair_reserved))
        accepted_column=False
        try:
            access_pads=[d for d in pads if not d.IsOnLayer(column_layer)]
            # Empty access is a proven no-op. Any real access request may
            # append copper or refresh launches outside this router.
            if access_pads:column_router=None
            ports=seed_plane_access(board,access_pads,deadline=deadline,
                track_width_nm=width,via_to_smd_clearance_nm=350000)
            by_id={t.m_Uuid.AsString():t for t in board.GetTracks()}
            port_lookup={(row['ref'],row['pin']):row for row in ports}
            terminals=[];complete=True
            for pad in pads:
                if pad.IsOnLayer(column_layer): terminals.append(xy(pad.GetPosition()));continue
                port=port_lookup.get((pad.GetParentFootprint().GetReference(),pad.GetNumber()))
                if not port or not port['connected']: complete=False;break
                terminals.append(xy(by_id[port['via_id']].GetPosition()))
            if column_router is None:
                column_router=KeyboardRouter(board,deadline=deadline,compact=True,track_width_nm=width)
                column_router.grid_step=250000;column_router.connection_budget_s=2;column_router.heuristic_weight=3
                column_router.path_policy=FieldPathPolicy('column');column_router.allow_penalty_fallback=False
            router=column_router;plans=[];intervals=[];incomplete_reason=None
            for a,b in zip(sorted(terminals,key=lambda q:(q[1],q[0])),sorted(terminals,key=lambda q:(q[1],q[0]))[1:]):
                if not complete: break
                choices=[]
                from .field_geometry import column_paths as backbone_paths,horizontal_length
                paths=backbone_paths(a,b)
                # Avoid a full per-layer bridge search when a clean front-face
                # stagger transition exists between the physical switch bodies.
                ranked=sorted(((sum(math.dist(x,z) for x,z in zip(path,path[1:])),path)
                               for path in paths),key=lambda candidate:candidate[0])
                for length,path in ranked:
                    if all(router.clear(p.SHAPE_SEGMENT(vec(x),vec(z),width),column_layer,net)
                           for x,z in zip(path,path[1:])):
                        choices.append({'paths':[(path,column_layer)],'vias':[],'cost':length})
                        break
                for path in ([] if choices else paths):
                    legs=[axial_path(router,x,z,column_layer,net) for x,z in zip(path,path[1:])]
                    if all(leg is not None for leg in legs):
                        choices.append({'paths':[q for leg in legs for q in leg['paths']],
                            'vias':[q for leg in legs for q in leg['vias']], 'cost':sum(leg['cost'] for leg in legs)})
                if not choices:
                    if router.expired():
                        incomplete_reason='unresolved_budget';complete=False;break
                    connection_keep={t.m_Uuid.AsString() for t in board.GetTracks()}
                    router.deadline=min(deadline,time.monotonic()+3)
                    router.__dict__.pop('last_search',None);router.__dict__.pop('last_plan',None)
                    try:joined=router.connect(Endpoint(a,column_layer,net),Endpoint(b,column_layer,net))
                    finally:router.deadline=deadline
                    if joined:
                        emitted=[t for t in board.GetTracks() if t.m_Uuid.AsString() not in connection_keep and t.GetClass()=='PCB_TRACK']
                        if any(t.GetStart().y==t.GetEnd().y and t.GetStart().x!=t.GetEnd().x for t in emitted):
                            incomplete_reason='horizontal_backbone_candidate_rejected';complete=False;break
                        intervals.append({'start_nm':a,'end_nm':b,'policy':'V+45_only','horizontal_nm':0,'fallback':'bounded_native_search','physical_impossibility_proved':False})
                        continue
                    if router.generation.name=='dsn-process-v2' and not router.expired():
                        alternative=_column_event_bridge(router,a,b,column_layer,net)
                        if alternative is not None:
                            commit_plan(router,alternative,net);plans.append(alternative)
                            intervals.append(dict(start_nm=a,end_nm=b,policy='V+45_only',horizontal_nm=0,fallback='event-rail-corner-vias',layer_bridges=len(alternative['vias'])))
                            continue
                    incomplete_reason='unresolved_budget' if router.expired() else 'bounded_search_exhausted'
                    complete=False;break
                plan=min(choices,key=lambda q:q['cost'])
                assert all(horizontal_length(path)==0 for path,side in plan['paths'])
                if router.expired():
                    incomplete_reason='unresolved_budget';complete=False;break
                commit_plan(router,plan,net);plans.append(plan)
                intervals.append({'start_nm':a,'end_nm':b,'policy':'V+45','horizontal_nm':sum(horizontal_length(path) for path,side in plan['paths']),'layer_bridges':len(plan['vias'])})
            if complete:
                ids=sorted({t.m_Uuid.AsString() for t in board.GetTracks()}-keep);protected.extend(ids)
                actual_vias=sum(isinstance(t,p.PCB_VIA) and t.m_Uuid.AsString() in ids for t in board.GetTracks())
                report['columns'].append({'net':net,'complete':True,'pad_count':len(pads),
                    'bridge_vias':actual_vias-len(ports),'access_vias':len(ports),'column_intervals':intervals,'object_ids':ids,'access_ids':[uid for item in ports for uid in item['ids']]})
                accepted_column=True
            else:
                report['columns'].append({'net':net,'complete':False,'reason':incomplete_reason or 'unresolved_terminal_access','event_bridge_diagnostic':getattr(router,'last_column_bridge',None),'column_intervals':intervals,'physical_impossibility_proved':False})
        finally:
            if not accepted_column:
                column_router=None
                rollback(keep);board.BuildConnectivity()
                if inventory is not None:inventory.ports,inventory.resolved,inventory.repair_reserved=launches
    report['overlap_cleanup']={'deferred':True,'reason':'historical optional helper absent from current stack'}
    board.BuildConnectivity();after=snapshot(board)
    assert original['footprints']==after['footprints']
    assert all(after['tracks'].get(uid)==shape for uid,shape in original['tracks'].items())
    require_via_land_clearance(board,deadline=deadline+10)
    report['protected_ids']=sorted(set(protected));report['source_copper_unchanged']=True
    return report


def _row_obstacles(router,left,right,layer,net):
    """Expanded native-shape Y events, clipped to this lane's X extent."""
    events=[];seen=set();margin=router.track_width//2
    def add(shape,gap):
        box=shape.BBox();extra=margin+gap+1000
        if box.GetRight()+extra>=left-margin*10 and box.GetLeft()-extra<=right+margin*10:
            events.append((box.GetTop()-extra,box.GetBottom()+extra))
    for entries in router.buckets[layer].values():
        for uid,shape,other,gap in entries:
            if uid in seen or other==net:continue
            seen.add(uid);add(shape,max(gap,router.net_clearances.get(net,0),router.minimum_clearance))
    seen=set()
    for entries in router.holes.values():
        for uid,shape,other in entries:
            if uid in seen:continue
            seen.add(uid);add(shape,router.hole_clearances[uid])
    for shape in router.edges:add(shape,router.edge_clearance)
    return events


def build_field(board,cfg,*,row_layer,column_layer,deadline):
    """Construct and independently check only the key field; no MCU feeders."""
    from collections import defaultdict
    from smilepcb.native_worker import snapshot
    from smilepcb.keyboard_phases import ordered_nets
    from .native_graph import copper_graph
    from copy import deepcopy
    from .interface_launches import current_launch_inventory
    if board.GetCopperLayerCount()!=2 or {row_layer,column_layer}!={p.F_Cu,p.B_Cu}:
        raise ValueError('Matrix field needs opposite outer faces on two copper layers')
    before=snapshot(board)
    inventory=current_launch_inventory()
    launches=None if inventory is None else (deepcopy(inventory.ports),set(inventory.resolved),set(inventory.repair_reserved))
    if time.monotonic()>=deadline:
        return {'complete':False,'budget_exhausted':True,'owned_ids':[],'roles':{},'field_groups':{}}
    phases=dict(ordered_nets(cfg));fps={f.GetReference():f for f in board.GetFootprints()};groups={name:defaultdict(list) for name in ('rows','columns')};required={};row_key_y=defaultdict(list)
    for key in cfg['layout']['keys']:
        for family,ref,face in [('rows','D'+str(key['id']),row_layer),('columns','SW'+str(key['id']),column_layer)]:
            pads=[d for d in fps[ref].Pads() if d.GetNumber() and d.GetNetname() in phases[family]]
            if not pads:raise ValueError('A key has no actual matrix terminal')
            if len({d.GetNetname() for d in pads})!=1:raise ValueError('Ambiguous physical matrix terminals')
            pad=min(pads,key=lambda d:(not(d.IsOnLayer(p.F_Cu) and d.IsOnLayer(p.B_Cu)),not d.IsOnLayer(face),d.GetPosition().x,d.GetPosition().y,d.GetNumber()))
            groups[family][pad.GetNetname()].append(pad)
            if family=='rows':row_key_y[pad.GetNetname()].append(fps['SW'+str(key['id'])].GetPosition().y)
            for d in pads:required[d.m_Uuid.AsString()]=(family,d.GetNetname())
    try:
        report=_seed_groups(board,rows=groups['rows'],columns=groups['columns'],row_key_y=row_key_y,row_layer=row_layer,column_layer=column_layer,deadline=deadline-10)
        graph=copper_graph(board,set(phases['rows']+phases['columns']),deadline);found=defaultdict(set);covered=set()
        for i,node in enumerate(graph['nodes']):
            uid=node['item'].m_Uuid.AsString()
            if uid in required:found[required[uid]].add(graph['find'](i));covered.add(uid)
        if covered!=set(required):raise ValueError('Native field proof missed physical terminal lands')
        report['field_groups']={family:{net:len(found[family,net]) for net in groups[family]} for family in groups};report['physical_pad_count']=len(required);report['terminals']={uid:list(binding) for uid,binding in required.items()}
        report['complete']=all(n==1 for nets in report['field_groups'].values() for n in nets.values()) and all(row['complete'] for family in groups for row in report[family])
        report['roles']={};after=snapshot(board)
        for family in groups:
            for row in report[family]:
                if not row['complete']:continue
                access=set(row.get('access_ids',[]))
                for uid in row['object_ids']:
                    item=after['tracks'][uid];role='column_access' if family=='columns' and uid in access else 'column_trunk' if family=='columns' else 'row_trunk' if uid in row['trunk_ids'] else 'row_link' if uid in row.get('link_ids',()) else 'row_access'
                    report['roles'][uid]=role
        report['owned_ids']=sorted(report['roles']);report['source_copper_preserved']=all(after['tracks'].get(u)==t for u,t in before['tracks'].items());report['footprints_preserved']=before['footprints']==after['footprints'];report['budget_exhausted']=time.monotonic()>=deadline
        report['generated_owned_ids']=sorted(set(report['roles'])-before['tracks'].keys())
        if not report['source_copper_preserved'] or not report['footprints_preserved']:raise ValueError('Matrix construction modified source objects')
        return report
    except Exception:
        for item in list(board.GetTracks()):
            if item.m_Uuid.AsString() not in before['tracks']:board.Delete(item)
        board.BuildConnectivity()
        if inventory is not None:inventory.ports,inventory.resolved,inventory.repair_reserved=launches
        raise
