"""Atomic Native admission of two true-offset lanes on one shared corridor."""
import math
import time


def try_common_pair(board,nets,*,source_ref,target_ref,layer,deadline,
                    track_width_nm=200000,max_length_nm=...):
    # Discovery is optional too: its deadline must not suppress the caller's
    # reserved fallback. All mutating attempts below own their exact rollback.
    try:
        return _try_common_pair(board,nets,source_ref=source_ref,target_ref=target_ref,
            layer=layer,deadline=deadline,track_width_nm=track_width_nm,max_length_nm=max_length_nm)
    except TimeoutError:return None


def _try_common_pair(board,nets,*,source_ref,target_ref,layer,deadline,
                     track_width_nm,max_length_nm):
    from smilepcb.native_worker import p,snapshot
    from smilepcb.usb_policy import resolve_usb_max_length_nm,usb_length_allowed
    from smilepcb.usb_pair import balance_pair
    from .common_rails import common_rail_spines,terminal_heading
    from .pair_paths import paired_spine_paths
    from .bundle_geometry import paths_clear
    from .follow_geometry import direction
    from .local_router import LocalRouter,xy,vec
    from .native_graph import copper_graph
    from .completion import component_frontiers
    max_length_nm=resolve_usb_max_length_nm(max_length_nm)
    if len(nets)!=2 or len(set(nets))!=2:raise ValueError('Two distinct pair nets required')
    if time.monotonic()>=deadline:return None
    before=snapshot(board);fps={f.GetReference():f for f in board.GetFootprints()}
    starts=[];ends=[];closed=[]
    for net in nets:
        graph=copper_graph(board,{net},deadline)
        indices={q['item'].m_Uuid.AsString():i for i,q in enumerate(graph['nodes'])}
        group=next(rows for rows in graph['groups'].values() if any(isinstance(graph['nodes'][i]['item'],p.PAD) and graph['nodes'][i]['item'].GetParentFootprint().GetReference()==source_ref for i in rows))
        target=next(d for d in fps[target_ref].Pads() if d.GetNetname()==net)
        if not target.IsOnLayer(layer):return None
        closed.append(graph['find'](indices[target.m_Uuid.AsString()])==graph['find'](group[0]))
        starts.append(component_frontiers(graph,group,deadline));ends.append(xy(target.GetPosition()))
    if all(closed):return {net:dict(added_ids=[],method='already connected') for net in nets}
    if any(closed):return None
    arrival=terminal_heading(ends,xy(fps[target_ref].GetPosition()),departure=False)
    if arrival is None or sum(q*q for q in arrival)!=1:return None
    router=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=track_width_nm)
    gap=max(router.minimum_clearance,*(router.net_clearances.get(n,0) for n in nets))
    seen=set();obstacles=[]
    def retain(uid,shape):
        if uid in seen:return
        seen.add(uid);b=shape.BBox();box=(b.GetLeft(),b.GetTop(),b.GetRight(),b.GetBottom())
        if box[0]<box[2] and box[1]<box[3]:obstacles.append(box)
    for entries in router.buckets[layer].values():
        for uid,shape,net,_ in entries:
            if net not in nets:retain(('copper',uid),shape)
    for entries in router.holes.values():
        for uid,shape,_ in entries:retain(('hole',uid),shape)
    # Milling and edge cuts are event rails too, not only terminal obstacles.
    for i,shape in enumerate(router.edges):retain(('edge',i),shape)
    b=board.GetBoardEdgesBoundingBox();bounds=(b.GetLeft(),b.GetTop(),b.GetRight(),b.GetBottom())
    centre=lambda rows:tuple(round(sum(q[i] for q in rows)/len(rows)) for i in (0,1))
    def outgoing(point,net):
        headings={direction(xy(t.GetEnd()),xy(t.GetStart())) if xy(t.GetStart())==point else direction(xy(t.GetStart()),xy(t.GetEnd()))
            for t in board.GetTracks() if isinstance(t,p.PCB_TRACK) and not isinstance(t,(p.PCB_VIA,p.PCB_ARC)) and t.GetNetname()==net and t.GetLayer()==layer and point in (xy(t.GetStart()),xy(t.GetEnd()))}
        return next(iter(headings)) if len(headings)==1 else None
    candidates={}
    for a in starts[0]:
        for z in starts[1]:
            if time.monotonic()>=deadline:return None
            hs=[outgoing(q,n) for q,n in zip((a,z),nets)]
            heading=hs[0] if hs[0] is not None and hs[0]==hs[1] else terminal_heading([a,z],xy(fps[source_ref].GetPosition()),departure=True)
            if heading is None or max(map(abs,heading))!=1:continue
            normal=(-heading[1],heading[0]);span=abs(sum((z[i]-a[i])*normal[i] for i in (0,1)))/math.hypot(*normal)
            radius=math.ceil(span/2+track_width_nm/2+max(gap,router.edge_clearance)+20000)
            for lead in sorted({radius,*[k*(track_width_nm+gap) for k in (1,2,3)]}):
                spines=common_rail_spines(centre([a,z]),centre(ends),source_heading=heading,arrival_heading=arrival,
                    bounds_nm=bounds,obstacle_bounds_nm=obstacles,envelope_radius_nm=radius,lead_nm=lead,max_candidates=32,deadline=deadline)
                for spine in spines:
                    paths=paired_spine_paths([a,z],ends,spine,source_heading=heading,arrival_heading=arrival,target_lead_nm=radius,min_body_segment_nm=track_width_nm+gap)
                    if paths is None or not paths_clear(paths,[track_width_nm]*2,gap):continue
                    key=tuple(tuple(q) for q in paths)
                    candidates[key]=(sum(len(q) for q in paths),sum(math.dist(x,y) for q in paths for x,y in zip(q,q[1:])),paths,spine,heading)
    try:
        for _,_,paths,spine,heading in sorted(candidates.values())[:128]:
            if time.monotonic()>=deadline:return None
            oracle=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=track_width_nm)
            if not all(oracle.clear(p.SHAPE_SEGMENT(vec(a),vec(z),track_width_nm),layer,n) for n,path in zip(nets,paths) for a,z in zip(path,path[1:])):continue
            accepted=False
            try:
                for net,path in zip(nets,paths):oracle.add_path(path,layer,net)
                added=set(snapshot(board)['tracks'])-set(before['tracks'])
                lengths={n:sum(t.GetLength() for t in board.GetTracks() if t.GetNetname()==n) for n in nets}
                short=min(lengths,key=lengths.get);target=ends[nets.index(short)]
                tunable=sorted([t for t in board.GetTracks() if t.m_Uuid.AsString() in added and t.GetNetname()==short],
                    key=lambda t:(min(math.dist(xy(t.GetStart()),target),math.dist(xy(t.GetEnd()),target)),-t.GetLength(),xy(t.GetStart())))
                check=LocalRouter(board,deadline=deadline,compact=True)
                def clear(a,z,width,face,net):return check.clear(p.SHAPE_SEGMENT(vec(a),vec(z),width),face,net)
                balance=None
                for item in tunable:
                    try:
                        balance=balance_pair(board,nets,editable_ids=[item.m_Uuid.AsString()],clear_segment=clear,deadline=deadline,max_length_nm=max_length_nm)
                        break
                    except ValueError:continue
                actual={n:sum(t.GetLength() for t in board.GetTracks() if t.GetNetname()==n) for n in nets}
                if not all(usb_length_allowed(q,max_length_nm) for q in actual.values()) or abs(actual[nets[0]]-actual[nets[1]])>=500000:continue
                graph=copper_graph(board,set(nets),deadline)
                if len(graph['groups'])!=2:continue
                after=snapshot(board)
                if after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=q for u,q in before['tracks'].items()):raise RuntimeError('Common pair changed protected source')
                added=set(after['tracks'])-set(before['tracks']);proof=LocalRouter(board,deadline=deadline,compact=True)
                if not all(proof.clear(t.GetEffectiveShape(layer),layer,t.GetNetname()) for t in board.GetTracks() if t.m_Uuid.AsString() in added):continue
                if time.monotonic()>=deadline:return None
                def final_path(net,start,end):
                    adjacency={};remaining=set()
                    for uid in added:
                        q=after['tracks'][uid]
                        if q['net']!=net:continue
                        a,z=tuple(q['start']),tuple(q['end']);remaining.add(uid)
                        adjacency.setdefault(a,[]).append((z,uid));adjacency.setdefault(z,[]).append((a,uid))
                    points=[tuple(start)]
                    while points[-1]!=tuple(end):
                        choices=[(q,u) for q,u in adjacency.get(points[-1],()) if u in remaining]
                        if len(choices)!=1:raise ValueError('Owned pair is not one unambiguous final path')
                        q,u=choices[0];remaining.remove(u);points.append(q)
                    if remaining:raise ValueError('Owned pair has unreported copper')
                    return points
                result={n:dict(added_ids=sorted(u for u in added if after['tracks'][u]['net']==n),
                    points_nm=final_path(n,path[0],path[-1]),proposal_points_nm=path,balance=balance,
                    method='joint common rail',common_spine_nm=spine,source_heading=heading) for n,path in zip(nets,paths)}
                accepted=True
                return result
            except (ValueError,TimeoutError):pass
            finally:
                if not accepted:
                    for t in list(board.GetTracks()):
                        if t.m_Uuid.AsString() not in before['tracks']:board.Delete(t)
                    board.BuildConnectivity()
                    if snapshot(board)!=before:raise RuntimeError('Common pair rollback changed protected source')
        return None
    except TimeoutError:return None
