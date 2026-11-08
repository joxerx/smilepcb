"""Joint lane proposals with physically checked two-layer obstacle bridges."""
import math,time
from smilepcb.native_worker import p,snapshot
from .cross_layer_polish import extract_spanning_chains
from .bundle_geometry import neighbor_groups,bundle_candidates,bundle_score,quality_improves,preserves_clean_columns,stemmed_chain
from .bundle_polish import _box,_joins_clear,retained_contacts_clear
from .visual_polish import _parts,_copy,_refill
from .local_router import LocalRouter,vec
from .layer_policy import terminal_layers
from .via_junction_polish import _pad_contacts_clear,_via_own_clear
from .column_bridges import bridge_carrier
from .visual_geometry import safe_route
from .follow_geometry import simplify


def refine_bridge_bundles(board,*,nets,preferred_layer,protected_ids,deadline,focus_bounds,deferred_zone_ids=(),preserve_v45_nets=(),max_groups=16,progress=None,quality_gate=None,free_corridors=False,movable_nets=None,preferred_layers=None):
    """Replan a whole parallel group; a flat crossing is not an automatic veto."""
    if free_corridors:
        from .free_bundles import refine_free_bundles
        return refine_free_bundles(board,nets=nets,preferred_layer=preferred_layer,protected_ids=protected_ids,deadline=deadline,focus_bounds=focus_bounds,deferred_zone_ids=deferred_zone_ids,preserve_v45_nets=preserve_v45_nets,max_groups=max_groups,progress=progress,quality_gate=quality_gate,movable_nets=movable_nets,preferred_layers=preferred_layers)
    report={'changed_groups':0,'rewrites':[],'attempts':[]}
    if deadline-time.monotonic()<15:return dict(report,reason='No proof reserve; incumbent retained')
    if preferred_layer not in (p.F_Cu,p.B_Cu) or len(focus_bounds)!=4 or focus_bounds[0]>=focus_bounds[2] or focus_bounds[1]>=focus_bounds[3]:raise ValueError('Expected two-layer physical MCU scope')
    protected=set(protected_ids);parts=_parts(board,deadline);stop=deadline-12
    work=extract_spanning_chains(board,eligible_nets=set(nets),protected_ids=protected,deadline=stop,deferred_zone_ids=deferred_zone_ids)
    def intersects(row):
        b=_box(row['path']);return b[0]<=focus_bounds[2] and b[2]>=focus_bounds[0] and b[1]<=focus_bounds[3] and b[3]>=focus_bounds[1]
    work=[dict(q,layer=preferred_layer) for q in work if intersects(q) and sum(math.dist(a,b) for a,b in zip(q['path'],q['path'][1:]))>=2000000]
    groups=neighbor_groups(work,limit=max_groups,deadline=stop);report.update(chain_count=len(work),group_count=len(groups))
    length=lambda path:sum(math.dist(a,b) for a,b in zip(path,path[1:]))
    for index,group in enumerate(groups):
        if stop-time.monotonic()<2:break
        ids={u for q in group for u in q['ids']};items={t.m_Uuid.AsString():t for t in board.GetTracks()}
        if sum(len(q['ids']) for q in group)!=len(ids) or not ids<=items.keys():continue
        original=snapshot(board);restore=[_copy(items[u]) for u in ids];names=[q['net'] for q in group];widths=[q['width'] for q in group];old=[q['path'] for q in group]
        allowance=min(stop,time.monotonic()+8);discovery=LocalRouter(board,compact=True,ignored_copper=ids,track_width_nm=min(widths),deadline=allowance,deferred_zone_ids=deferred_zone_ids);gap=max(discovery.minimum_clearance,*[discovery.net_clearances.get(n,0) for n in names])
        score=lambda paths:bundle_score(paths,widths,clearance_nm=gap,bend_price_nm=3000000,group_strength=3,nets=names)
        original_score=score(old);old_vias=sum(original['tracks'][u]['class']=='PCB_VIA' for u in ids);old_off=sum(length(leg) for q in group for face,leg in q['old_legs'] if face!=preferred_layer);old_cost=original_score+old_off*4+old_vias*400000
        def stem_length(row,point,face):
            values=[]
            for fp in board.GetFootprints():
                for pad in fp.Pads():
                    if pad.GetNetname()==row['net'] and pad.GetAttribute()==p.PAD_ATTRIB_SMD and pad.IsOnLayer(face) and p.SHAPE.Collide(p.SHAPE_CIRCLE(vec(point),1),pad.GetEffectiveShape(face),0):
                        size=pad.GetSize();values.append(max(size.x,size.y)/2+row['width']/2+gap)
            return max(values,default=0)
        stems=[stemmed_chain(q['path'],stem_length(q,q['path'][0],q['start_layer']),stem_length(q,q['path'][-1],q['end_layer'])) for q in group]
        inner=[dict(q,path=stem[1]) for q,stem in zip(group,stems)]
        proposals=bundle_candidates(inner,clearance_nm=gap,limit=24,deadline=allowance,bend_price_nm=3000000,group_strength=3,allow_layer_bridges=True)
        for candidate in proposals:
            candidate['paths']=[simplify([*stem[0],*body[1:],*stem[2][1:]]) for stem,body in zip(stems,candidate['paths'])]
            candidate['score']=score(candidate['paths'])
        proposals=[q for q in proposals if all(preserves_clean_columns(row,path,set(preserve_v45_nets)) for row,path in zip(group,q['paths']))]
        proposals=[q for q in proposals if quality_improves(old,q['paths'],old_cost,q['score'])]
        trial={'nets':names,'candidates':len(proposals),'accepted':False,'tried':0,'reasons':[]};report['attempts'].append(trial)
        for proposal in sorted(proposals,key=lambda q:(sum(len(p) for p in q['paths']),q['score'],q['paths']))[:12]:
            if allowance-time.monotonic()<.5:break
            trial['tried']+=1;added=[];routes=[];okay=False;reason=None
            # Each plan has an isolated obstacle index; old group copper is
            # removed atomically, and each emitted arm indexes its new copper
            # before the next arm plans. This checks cross-arm via spacing too.
            live_items={t.m_Uuid.AsString():t for t in board.GetTracks()}
            restore=[_copy(live_items[u]) for u in ids]
            for u in ids:board.Delete(live_items[u])
            try:
                oracle=LocalRouter(board,compact=True,track_width_nm=min(widths),deadline=allowance,deferred_zone_ids=deferred_zone_ids)
                for row,path in zip(group,proposal['paths']):
                    net=row['net'];width=row['width'];oracle.track_width=width
                    access_a=terminal_layers(board,path[0],net,row['start_layer']);access_z=terminal_layers(board,path[-1],net,row['end_layer']);other=p.B_Cu if preferred_layer==p.F_Cu else p.F_Cu
                    route=bridge_carrier(path,preferred_layer,other,start_layers=access_a,end_layers=access_z,secondary_length_factor=5,via_cost_nm=400000,
                        clear=lambda a,b,l:oracle.clear(p.SHAPE_SEGMENT(vec(a),vec(b),width),l,net),
                        via_clear=lambda v:oracle.via_clear(v,net) and _via_own_clear(oracle,v,net,oracle.via_width),
                        via_pair_clear=lambda a,b:math.dist(a,b)>=oracle.via_width+oracle.minimum_clearance and oracle.via_pair_clear(a,b),deadline=allowance)
                    if route is None or not safe_route(route,width,oracle.via_width):raise ValueError('No native-clear joint obstacle bridge')
                    if any(not _joins_clear(board,leg,face,net,()) or not _pad_contacts_clear(board,leg,face,net,width,oracle) or not retained_contacts_clear(board,leg,face,net,width,oracle=oracle) for face,leg in route['legs']):raise ValueError('Joint carrier changes owned contacts')
                    for face,leg in route['legs']:oracle.add_path(leg,face,net)
                    for via in route['vias']:oracle.add_via(via,net)
                    routes.append(route)
                off=sum(length(leg) for q in routes for face,leg in q['legs'] if face!=preferred_layer);vias=sum(len(q['vias']) for q in routes);cost=score(proposal['paths'])+off*4+vias*400000
                if cost>=old_cost-100000:raise ValueError('Physical bridges erase the complete-group gain')
                _refill(board)
                if _parts(board,deadline)!=parts:raise ValueError('Joint route changed whole-board contacts')
                after=snapshot(board)
                if quality_gate is not None and not quality_gate(original['tracks'],after['tracks']):raise ValueError('Whole MCU area grouping would degrade')
                if after['footprints']!=original['footprints'] or any(after['tracks'].get(u)!=t for u,t in original['tracks'].items() if u not in ids):raise ValueError('Joint route changed retained copper')
                added=[u for u in after['tracks'] if u not in original['tracks']];okay=True
            except (ValueError,TimeoutError) as error:reason=str(error)
            except BaseException:
                for t in list(board.GetTracks()):
                    if t.m_Uuid.AsString() not in original['tracks']:board.Delete(t)
                live={t.m_Uuid.AsString() for t in board.GetTracks()}
                for t in restore:
                    if t.m_Uuid.AsString() not in live:board.Add(t)
                _refill(board)
                if snapshot(board)!=original:raise ValueError('Joint exception rollback lost source')
                raise
            if okay:
                report['changed_groups']+=1;trial.update(accepted=True,old_score=old_cost,new_score=cost);report['rewrites'].append({'nets':names,'old':old,'new':proposal['paths'],'routes':routes,'old_ids':sorted(ids),'new_ids':added,'old_score':old_cost,'new_score':cost});break
            for t in list(board.GetTracks()):
                if t.m_Uuid.AsString() not in original['tracks']:board.Delete(t)
            for t in restore:board.Add(t)
            _refill(board)
            if snapshot(board)!=original or _parts(board,deadline)!=parts:raise ValueError('Joint rejection rollback lost source')
            trial['reasons'].append(reason)
        if progress:progress({'stage':'bridge-bundles','index':index+1,'total':len(groups),'accepted':report['changed_groups']})
    if _parts(board,deadline)!=parts:raise ValueError('Joint final numbered-pad contacts changed')
    report['all_pad_partitions_exact']=True
    return report
