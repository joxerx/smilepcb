"""Joint MCU fanout refinement seeded from terminals and movable corridors."""
import math
import time

from smilepcb.native_worker import p,snapshot
from .cross_layer_polish import extract_spanning_chains
from .bundle_geometry import neighbor_groups,bundle_candidates,bundle_score,preserves_clean_columns,stemmed_chain
from .bundle_polish import _box
from .visual_polish import _parts
from .local_router import LocalRouter,vec
from .free_corridor_geometry import terminal_groups,corridor_candidates,restoration_cost_delta
from .corridor_native import attempt_corridor
from .follow_geometry import simplify
from .polish_geometry import shape_score


def refine_free_bundles(board,*,nets,preferred_layer,protected_ids,deadline,focus_bounds,
                        deferred_zone_ids=(),preserve_v45_nets=(),max_groups=16,
                        progress=None,quality_gate=None,movable_nets=None,preferred_layers=None):
    report=dict(changed_groups=0,rewrites=[],attempts=[],algorithm='terminal-banks-and-free-corridors')
    if deadline-time.monotonic()<15:return dict(report,reason='No proof reserve')
    if preferred_layer not in (p.F_Cu,p.B_Cu) or len(focus_bounds)!=4 or focus_bounds[0]>=focus_bounds[2] or focus_bounds[1]>=focus_bounds[3]:raise ValueError('Expected two-layer physical MCU scope')
    stop=deadline-12;parts=_parts(board,deadline);protected=set(protected_ids)
    initial=snapshot(board);protected|={u for u,t in initial['tracks'].items() if t['locked']}
    pool=extract_spanning_chains(board,eligible_nets=set(movable_nets or nets),protected_ids=protected,deadline=stop,deferred_zone_ids=deferred_zone_ids)
    def intersects(row):
        b=_box(row['path']);return b[0]<=focus_bounds[2] and b[2]>=focus_bounds[0] and b[1]<=focus_bounds[3] and b[3]>=focus_bounds[1]
    work=[dict(q,layer=preferred_layer) for q in pool if q['net'] in nets and intersects(q) and math.dist(q['path'][0],q['path'][-1])>=2000000]
    center=((focus_bounds[0]+focus_bounds[2])//2,(focus_bounds[1]+focus_bounds[3])//2)
    groups=[];seen=set()
    for group in [*terminal_groups(work,center,limit=max_groups),*neighbor_groups(work,limit=max_groups,deadline=stop)]:
        key=tuple(sorted(u for q in group for u in q['ids']))
        if key not in seen:seen.add(key);groups.append(group)
    report.update(chain_count=len(work),group_count=len(groups),terminal_group_count=len(terminal_groups(work,center,limit=max_groups)))
    length=lambda path:sum(math.dist(a,z) for a,z in zip(path,path[1:]))
    for index,group in enumerate(groups):
        if stop-time.monotonic()<2:break
        original=snapshot(board);ids={u for q in group for u in q['ids']}
        if not ids<=original['tracks'].keys():continue
        allowance=min(stop,time.monotonic()+max(8,min(45,(stop-time.monotonic())/max(1,len(groups)-index))))
        oracle=LocalRouter(board,compact=True,ignored_copper=ids,track_width_nm=min(q['width'] for q in group),deadline=allowance,deferred_zone_ids=deferred_zone_ids)
        gap=max(oracle.minimum_clearance,*[oracle.net_clearances.get(q['net'],0) for q in group]);names=[q['net'] for q in group];widths=[q['width'] for q in group]
        score=lambda paths:bundle_score(paths,widths,clearance_nm=gap,bend_price_nm=3000000,group_strength=3,nets=names)
        old=[q['path'] for q in group];old_score=score(old)
        old_vias=sum(original['tracks'][u]['class']=='PCB_VIA' for u in ids)
        old_off=sum(length(path) for q in group for face,path in q['old_legs'] if face!=preferred_layer)
        old_cost=old_score+old_off*4+old_vias*400000
        def stem_length(row,point,face):
            values=[]
            for fp in board.GetFootprints():
                for pad in fp.Pads():
                    if pad.GetNetname()==row['net'] and pad.GetAttribute()==p.PAD_ATTRIB_SMD and pad.IsOnLayer(face) and p.SHAPE.Collide(p.SHAPE_CIRCLE(vec(point),1),pad.GetEffectiveShape(face),0):
                        size=pad.GetSize();values.append(max(size.x,size.y)/2+row['width']/2+gap)
            return max(values,default=0)
        stems=[stemmed_chain(q['path'],stem_length(q,q['path'][0],q['start_layer']),stem_length(q,q['path'][-1],q['end_layer'])) for q in group]
        inner=[dict(q,path=stem[1]) for q,stem in zip(group,stems)]
        proposals=corridor_candidates(inner,clearance_nm=gap,limit=48,deadline=allowance)
        proposals+=bundle_candidates(inner,clearance_nm=gap,limit=24,deadline=allowance,bend_price_nm=3000000,group_strength=3,allow_layer_bridges=True)
        unique={}
        for proposal in proposals:
            paths=[simplify([*stem[0],*body[1:],*stem[2][1:]]) for stem,body in zip(stems,proposal['paths'])]
            if not all(preserves_clean_columns(row,path,set(preserve_v45_nets)) for row,path in zip(group,paths)):continue
            if paths==old:continue
            value=score(paths)
            if value>=old_cost-100000:continue
            unique[tuple(tuple(q) for q in paths)]=dict(paths=paths,score=value)
        trial=dict(nets=names,candidates=len(unique),tried=0,accepted=False,reasons=[],blocked_ids=[]);report['attempts'].append(trial)
        for proposal in sorted(unique.values(),key=lambda q:(q['score'],q['paths'])):
            if time.monotonic()+.5>=allowance:break
            trial['tried']+=1
            def accept(before,after,routes,restored):
                cost=proposal['score']+4*sum(length(path) for route in routes for face,path in route['legs'] if face!=preferred_layer)+400000*sum(len(route['vias']) for route in routes)
                for saved in restored:
                    cost+=restoration_cost_delta(saved,before['tracks'],preferred_layer=(preferred_layers or {}).get(saved['row']['net'],saved['row']['start_layer']))
                if cost>=old_cost-100000:return False
                if quality_gate is not None and not quality_gate(before['tracks'],after['tracks']):return False
                if _parts(board,deadline)!=parts:return False
                trial.update(old_score=old_cost,new_score=cost);return True
            result=attempt_corridor(board,group,proposal['paths'],preferred_layer=preferred_layer,protected_ids=protected,deadline=allowance,deferred_zone_ids=deferred_zone_ids,
                                    blocker_pool=[q for q in pool if intersects(q) and all(u in original['tracks'] for u in q['ids'])],accept=accept,preserve_v45_nets=preserve_v45_nets,preferred_layers=preferred_layers)
            if result['accepted']:
                report['changed_groups']+=1;trial['accepted']=True
                report['rewrites'].append(dict(nets=names,old=old,new=proposal['paths'],**result));break
            trial['reasons'].append(result['reason']);trial['blocked_ids']=sorted(set(trial['blocked_ids'])|set(result['blocked_ids']))
        if progress:progress(dict(stage='free-corridors',index=index+1,total=len(groups),accepted=report['changed_groups']))
    if _parts(board,deadline)!=parts:raise ValueError('Free bundles changed whole-board pad partitions')
    report['all_pad_partitions_exact']=True
    return report
