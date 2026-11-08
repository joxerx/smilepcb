"""Native joint repair: discover a target, displace owned copper, restore it.

No target identities, removal masks, routes, or corridors are accepted as
inputs. generated_ids is a complete positive provenance inventory. Unknown
copper and all source heads remain hard. A rejected attempt changes no file.
"""
from copy import deepcopy
import itertools
import json
import math
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from smilepcb.native_worker import p, snapshot
from .local_router import LocalRouter, xy, vec, paths
from .native_graph import copper_graph, nearest_to_node
from .port_policy import PortPolicy
from .grid_search import search_many
from .conditional_geometry import FrozenProofRouter, saved_via_clear_cached
from .field_contract import load, verify_native, MARKER
from .interface_contract import load_verified_interfaces
from .interface_launches import current_launch_inventory, reserve_launches
from .joint_repair import visibility_points, search_paths, movable_ids, expansion_masks, trial_schedule, bounded_region
from .joint_repair_contract import (NativeRepairResult, replace_field, component_obligations, require_components,
                                    require_partitions, publish_verified, splice_copper)


def _check(deadline):
    if time.monotonic()>=deadline:raise TimeoutError('Joint repair deadline exhausted')


def _groups(graph):
    return [{graph['nodes'][i]['item'].m_Uuid.AsString() for i in indices} for indices in graph['groups'].values()]


def _pads(graph):
    return [frozenset(graph['nodes'][i]['item'].m_Uuid.AsString() for i in indices
                     if isinstance(graph['nodes'][i]['item'],p.PAD) and graph['nodes'][i]['item'].GetNumber())
            for indices in graph['groups'].values()
            if any(isinstance(graph['nodes'][i]['item'],p.PAD) and graph['nodes'][i]['item'].GetNumber() for i in indices)]


class _RetainedBoard:
    def __init__(self,board,ids):self.board=board;self.ids=ids
    def GetTracks(self):return [q for q in self.board.GetTracks() if q.m_Uuid.AsString() in self.ids]
    def __getattr__(self,name):return getattr(self.board,name)


def _physical(board,ids=None):
    return FrozenProofRouter._physical_state(SimpleNamespace(board=board if ids is None else _RetainedBoard(board,ids)))


def _seal(board):
    return (snapshot(board),_physical(board),FrozenProofRouter._rule_state(SimpleNamespace(board=board)),board.GetFileName())


class _Region:
    def __init__(self,points,padding=4000000):
        self.bounds=bounded_region(points,padding=padding,max_span=40000000)
    def contains(self,q,radius=0):
        l,t,r,b=self.bounds
        return l+radius<=q[0]<=r-radius and t+radius<=q[1]<=b-radius
    def accepts_path(self,path):return all(self.contains(q) for q in path)


def _router(board,region,width,deadline,ignored=()):
    class RegionRouter(LocalRouter):
        def via_clear(self,position,net):
            return region.contains(position,self.via_width//2) and super().via_clear(position,net)
    router=RegionRouter(board,deadline=deadline,compact=True,track_width_nm=width,
                        ignored_copper=ignored,path_policy=region)
    # index(new copper) uses the bound generation minimum plus own_clearance's
    # numerical guard. Planning must anticipate that same emitted-net limit.
    router.net_clearances={n:max(g,router.minimum_clearance+1000) for n,g in router.net_clearances.items()}
    router.grid_step=50000;router.search_budget_s=6;router.search_expansion_limit=30000
    router.via_cost_nm=1000000;router.layer_change_heuristic_nm=0;router.heuristic_weight=1
    if router.port_policy is None:router.port_policy=PortPolicy(board,[],track_width_nm=width)
    return router


def _contacts(router,graph,root,anchors,region,limit=64):
    contacts=set()
    for anchor in anchors:contacts.update(router.port_policy.contacts(graph,graph['groups'][root],anchor))
    return sorted((q for q in contacts if region.contains(q[0],router.track_width//2)),
                  key=lambda q:(min(math.dist(q[0],a) for a in anchors),q))[:limit]


def _targets(graph,excluded,deadline):
    groups={};nodes=graph['nodes']
    for root,indices in graph['groups'].items():
        _check(deadline)
        pads={nodes[i]['item'].m_Uuid.AsString() for i in indices
              if isinstance(nodes[i]['item'],p.PAD) and nodes[i]['item'].GetNumber()}
        if pads and nodes[indices[0]]['net'] not in excluded:
            groups.setdefault(nodes[indices[0]]['net'],[]).append((root,pads))
    choices=[]
    for net,rows in groups.items():
        for (a,pa),(b,pb) in itertools.combinations(rows,2):
            aa=[q for i in graph['groups'][a] for q in nodes[i]['points']]
            bb=[q for i in graph['groups'][b] for q in nodes[i]['points']]
            distance,x,y=min((math.dist(x,y),x,y) for x in aa for y in bb)
            if distance<=30000000:choices.append((distance,x,y,net,a,b,pa|pb))
    return sorted(choices,key=lambda row:row[:4])[:6]


def _obstacles(router,net,region,deadline):
    rows={};items={q.m_Uuid.AsString():q for q in router.board.GetTracks()}
    for buckets in router.buckets.values():
        for values in buckets.values():
            for uid,shape,other,gap in values:
                _check(deadline)
                if other==net:continue
                box=shape.BBox();bounds=(box.GetLeft(),box.GetTop(),box.GetRight(),box.GetBottom())
                l,t,r,b=region.bounds
                if bounds[2]<l or bounds[0]>r or bounds[3]<t or bounds[1]>b:continue
                key=(uid,bounds)
                if key in rows:continue
                row={'bounds':bounds,'clearance':max(gap,router.net_clearances.get(net,0),router.minimum_clearance+1000)}
                item=items.get(uid)
                if item is not None and isinstance(item,p.PCB_VIA):
                    row.update(center=xy(item.GetPosition()),radius=max(item.GetWidth(face) for face in (p.F_Cu,p.B_Cu) if item.IsOnLayer(face))//2)
                rows[key]=row
    center=((region.bounds[0]+region.bounds[2])//2,(region.bounds[1]+region.bounds[3])//2)
    return sorted(rows.values(),key=lambda q:(math.dist(center,((q['bounds'][0]+q['bounds'][2])/2,(q['bounds'][1]+q['bounds'][3])/2)),q['bounds']))[:384]


def _plans(board,graph,target,field,generated,protected,deadline):
    _,a,b,net,ra,rb,pads=target;region=_Region([a,b]);width=max(board.GetDesignSettings().m_TrackMinWidth,150000)
    if max(region.bounds[2]-region.bounds[0],region.bounds[3]-region.bounds[1])>40000000:return [],region
    soft=movable_ids(snapshot(board),field,generated,protected=protected,target_net=net)
    world=_router(board,region,width,deadline);world.refresh_interfaces(graph)
    relaxed=_router(board,region,width,deadline,soft);relaxed.refresh_interfaces(graph)
    starts=_contacts(world,graph,ra,[b],region,32);goals=_contacts(world,graph,rb,[a],region,32)
    if not starts or not goals:return [],region
    components={}
    for indices in graph['groups'].values():
        ids=[graph['nodes'][i]['item'].m_Uuid.AsString() for i in indices]
        identity='component:'+min(ids)
        components.update((uid,identity) for uid in ids)
    plans,region.diagnostic=_plan_geometry(world,relaxed,starts,goals,net,region,soft,deadline,blocker_components=components)
    for plan in plans:plan.update(net=net,width=width,target_pads=pads,soft=soft)
    return plans,region


def _plan_geometry(world,relaxed,starts,goals,net,region,soft,deadline,*,max_results=8,max_points=768,contact_projection_limit=None,blocker_components=None,path_admission=None,turn_admission=None,plan_admission=None):
    width=world.track_width
    # Preserve every admitted terminal; bound secondary obstacle projections
    # so dense component inventories do not consume the whole search deadline.
    representatives=[q[0] for q in starts+goals]
    if contact_projection_limit is not None:
        representatives=[]
        for rows in (starts,goals):
            for face in (p.F_Cu,p.B_Cu):
                representatives.extend([q[0] for q in rows if q[1]==face][:contact_projection_limit])
    portal_diagnostic={}
    try:
        points=visibility_points(representatives,_obstacles(world,net,region,deadline),
                                 bounds=region.bounds,width=width,via_width=world.via_width,max_points=max_points,diagnostic=portal_diagnostic)
    except ValueError as error:
        return [],{'reason':'visibility_capacity','detail':str(error),'starts':len(starts),'goals':len(goals),'movable':len(soft)}
    selected_count=len(points)
    points=sorted(set(points)|{tuple(q[0]) for q in starts+goals})
    portal_diagnostic.update(terminal_vertices_added=len(points)-selected_count,selected_points_nm=[list(q) for q in points])
    def edge(a,b,layer):
        if not region.accepts_path([a,b]):return None
        candidates=[]
        candidates_paths=paths(a,b,octile_only=True)
        if path_admission is not None:candidates_paths=(path for path in candidates_paths if path_admission(path))
        for path in itertools.islice(candidates_paths,5):
            if time.monotonic()>=deadline:break
            path=[q for i,q in enumerate(path) if not i or q!=path[i-1]]
            if len(path)<2:continue
            hits=set()
            for x,y in zip(path,path[1:]):
                hits.update(world.collisions(p.SHAPE_SEGMENT(vec(x),vec(y),width),layer,net))
                if not hits<=soft:break
            if hits<=soft:
                cost=sum(math.dist(x,y) for x,y in zip(path,path[1:]))
                candidates.append((len(hits),cost,path,hits))
        if not candidates:return None
        _,cost,path,hits=min(candidates,key=lambda q:q[:3])
        return {'path':path,'blockers':hits,'cost':cost}
    def via(point):
        if time.monotonic()>=deadline or not relaxed.via_clear(point,net):return None
        hits=set()
        for face in (p.F_Cu,p.B_Cu):hits.update(world.collisions(p.SHAPE_CIRCLE(vec(point),world.via_width//2),face,net,True))
        return hits if hits<=soft else None
    diagnostic={'points':len(points),'starts':len(starts),'goals':len(goals),'movable':len(soft),'portals':portal_diagnostic}
    try:
        plans=search_paths(points,starts,goals,edge,via,soft_ids=soft,deadline=deadline,max_results=max_results,displacement_cost=2*width,
                           via_pair=lambda a,b:math.dist(a,b)>=max(610000,world.drill+260000) and world.via_pair_clear(a,b),diagnostic=diagnostic,blocker_components=blocker_components,
                           turn_admission=turn_admission,plan_admission=plan_admission)
    except TimeoutError:plans=[]
    return plans,diagnostic


def _adjacency(graph,ids,deadline):
    nodes=graph['nodes'];result={}
    for node in nodes:
        uid=node['item'].m_Uuid.AsString()
        if uid not in ids:continue
        row=set()
        for other in nodes:
            _check(deadline);oid=other['item'].m_Uuid.AsString()
            if oid==uid or node['net']!=other['net']:continue
            if any(layer in other['shapes'] and p.SHAPE.Collide(shape,other['shapes'][layer],0) for layer,shape in node['shapes'].items()):row.add(oid)
        result[uid]=row
    return result


def _restoration_region(graph,removed,plan,ports,contacts,deadline):
    """Include actual cut attachments and original source launch extents.

    This derives a search region only. Source origins never become routing
    terminals: _contacts still obtains admitted contacts from PortPolicy.
    """
    nodes=graph['nodes'];index={q['item'].m_Uuid.AsString():i for i,q in enumerate(nodes)}
    missing=set(removed)-set(contacts)
    if missing:contacts.update(_adjacency(graph,missing,deadline))
    points=[tuple(q) for layer,path in plan['legs'] for q in path]+list(map(tuple,plan['vias']))
    boundary=set()
    for uid in removed:
        _check(deadline);node=nodes[index[uid]];points.extend(node['points'])
        for other in contacts.get(uid,()):
            if other in removed or other not in index:continue
            boundary.add(other);neighbor=nodes[index[other]]
            points.extend(nearest_to_node(neighbor,q) for q in node['points'])
    source_extents=0
    from .bank_geometry import terminal_origin
    for port in ports:
        # Remote heads reached through intact ordinary copper need no new
        # launch. Only an exposed source fragment needs its launch extent.
        if not boundary&set(port['pad_ids']+port['added_ids']):continue
        tip=tuple(port['exit_nm']);origin=terminal_origin(port);points.extend((tip,origin))
        dx,dy=tip[0]-origin[0],tip[1]-origin[1];length=math.hypot(dx,dy)
        if length:points.append((round(tip[0]+dx*1000000/length),round(tip[1]+dy*1000000/length)))
        source_extents+=1
    region=_Region(points,padding=2000000)
    return region,{'anchors':len(set(points)),'source_extents':source_extents,'bounds':region.bounds}


def _emit(router,plan):
    net=plan['net']
    # Check the full pending union before emitting; later insertions get the
    # same per-net creation guards and original source first-contact policy.
    from .grid_search import valid_plan
    if not valid_plan(router,plan['legs'],plan['vias'],net):raise ValueError('Discovered target geometry is blocked after exact removal')
    for kind,face,value in plan['actions']:
        if kind=='edge':router.add_path(value,face,net)
        else:router.add_via(value,net)


def _visibility_connection(router,starts,goals,net,region,deadline):
    """Hard-only exact portals; source/via predicates match target planning."""
    try:
        plans,diagnostic=_plan_geometry(router,router,starts,goals,net,region,set(),deadline,
                                        max_results=1,max_points=512,contact_projection_limit=4)
    except TimeoutError:
        plans=[];diagnostic={'reason':'deadline'}
    router.last_visibility=diagnostic
    if not plans:return False
    plan=plans[0];plan['net']=net
    if plan['removed']:raise ValueError('Restoration visibility attempted to move hard copper')
    _emit(router,plan)
    diagnostic.update(accepted=True,vias=len(plan['vias']),legs=len(plan['legs']))
    return True


def _restoration_expansion(router,graph,starts,goals,net,region,movable,before,removed,deadline):
    """Discover an owned foreign-blocker delta without emitting any copper."""
    live={q.m_Uuid.AsString() for q in router.board.GetTracks()}
    eligible={uid for uid in set(movable)&live-set(removed) if uid in before['tracks'] and before['tracks'][uid]['net']!=net}
    if not eligible:return {'removed_ids':[],'search':{'reason':'no_owned_foreign_candidates'}}
    relaxed=_router(router.board,region,router.track_width,deadline,ignored=eligible)
    relaxed.refresh_interfaces(graph)
    try:
        plans,diagnostic=_plan_geometry(router,relaxed,starts,goals,net,region,eligible,deadline,
                                        max_results=1,max_points=512,contact_projection_limit=4)
    except TimeoutError:
        return {'removed_ids':[],'search':{'reason':'deadline'}}
    if not plans:return {'removed_ids':[],'search':diagnostic}
    plan=plans[0];extra=set(plan['removed'])
    if not extra or not extra<=eligible or len(set(removed)|extra)>16:
        return {'removed_ids':[],'search':diagnostic,'reason':'no_bounded_strict_expansion'}
    from .grid_search import valid_plan
    if not valid_plan(relaxed,plan['legs'],plan['vias'],net):
        return {'removed_ids':[],'search':diagnostic,'reason':'relaxed_proposal_validation'}
    return {'removed_ids':sorted(extra),'legs':plan['legs'],'vias':plan['vias'],'search':diagnostic}


def _restore(board,obligations,original_nets,removed,before,field,region,nets,deadline,reverse=False,steps=None,*,movable=(),feedback=None):
    new_roles={};anchors=[];stop=min(deadline,time.monotonic()+5)
    for uid in removed:
        shape=before['tracks'][uid]
        anchors.extend(tuple(shape[key]) for key in ('start','end') if key in shape)
        if 'position' in shape:anchors.append(tuple(shape['position']))
    anchors=sorted(set(anchors))[:48]
    rows=[]
    for obligation in obligations:
        net=original_nets[next(iter(obligation))]
        if not any(before['tracks'][u]['net']==net for u in removed):continue
        roles={field['roles'][u] for u in removed&set(field['roles']) if before['tracks'][u]['net']==net}
        if len(roles)>1:raise ValueError('Mixed field roles require a separate scoped replacement')
        width=max([before['tracks'][u]['width'] for u in removed if before['tracks'][u]['net']==net and before['tracks'][u]['class']=='PCB_TRACK'] or [150000])
        rows.append((len(obligation),net,obligation,next(iter(roles),None),width))
    steps=[] if steps is None else steps
    # Small attachment sets tend to have the fewest exits. Reversed retry is
    # a separate fresh-board attempt, with no carried mutable launch state.
    rows.sort(key=lambda q:(q[0],q[1]),reverse=reverse)
    for _,net,obligation,role,width in rows:
        for count in range(len(obligation)):
            _check(stop);graph=copper_graph(board,nets,stop,include_zones=False)
            index={q['item'].m_Uuid.AsString():i for i,q in enumerate(graph['nodes'])}
            if not obligation<=index.keys():raise ValueError('Original retained conductor vanished')
            roots={graph['find'](index[u]) for u in obligation}
            if len(roots)==1:break
            router=_router(board,region,width,stop);router.search_budget_s=3;router.refresh_interfaces(graph)
            contacts={root:_contacts(router,graph,root,anchors,region) for root in roots}
            empty=[root for root in roots if not contacts[root]]
            if empty:
                steps.append({'net':net,'fragments':len(roots),'accepted':False,'reason':'region_inaccessible',
                    'empty_fragment_sizes':[len(graph['groups'][root]) for root in empty],
                    'contact_counts':{str(root):len(values) for root,values in contacts.items()},'region':region.bounds})
                raise ValueError('No admitted contacts inside complete cut/source region: '+net)
            pairs=sorted((math.dist(a[0],b[0]),ra,rb,a,b) for ra,rb in itertools.combinations(sorted(roots),2)
                         for a in contacts[ra] for b in contacts[rb])
            success=False
            for _,ra,rb,a,b in pairs[:48]:
                if router.expired():break
                if a[1]!=b[1]:continue
                path=router.leg(a[0],b[0],a[1],net)
                if path is not None:router.add_path(path,a[1],net);success=True;break
            proposal=None
            if not success and pairs and not router.expired():
                _,ra,rb,_,_=pairs[0]
                success=_visibility_connection(router,contacts[ra],contacts[rb],net,region,min(stop,time.monotonic()+(2 if movable else 3)))
            if not success and pairs and movable and feedback is not None and not router.expired():
                _,ra,rb,_,_=pairs[0]
                proposal=_restoration_expansion(router,graph,contacts[ra],contacts[rb],net,region,movable,before,removed,min(stop,time.monotonic()+2))
                if proposal['removed_ids']:
                    feedback.update(proposal)
                    steps.append({'net':net,'fragments':len(roots),'accepted':False,'added_ids':list(router.added),
                                  'visibility':getattr(router,'last_visibility',None),'expansion_proposal':proposal})
                    raise ValueError('Complete restoration proposes additional owned blockers: '+net)
            if not success and pairs and not router.expired():
                _,ra,rb,_,_=pairs[0]
                for step in (50000,25000):
                    if router.expired():break
                    router.grid_step=step
                    if search_many(router,contacts[ra],contacts[rb],net):success=True;break
            if role:new_roles.update((uid,role) for uid in router.added)
            steps.append({'net':net,'fragments':len(roots),'accepted':success,'added_ids':list(router.added),'search':getattr(router,'last_search',None),'visibility':getattr(router,'last_visibility',None),'expansion_proposal':proposal})
            if not success:raise ValueError('Could not restore complete conductor component: '+net)
            stop=min(deadline,time.monotonic()+5)
        else:raise ValueError('Component join count exhausted')
    return new_roles,steps


def _retire_single_layer_vias(board,allowed,protected,obligations,nets,deadline,owners):
    removed=set()
    for item in list(board.GetTracks()):
        _check(deadline);uid=item.m_Uuid.AsString()
        if uid not in allowed or uid in protected or not isinstance(item,p.PCB_VIA) or item.IsLocked():continue
        graph=copper_graph(board,{item.GetNetname()},deadline,include_zones=False);faces=set()
        for node in graph['nodes']:
            if node['item'].m_Uuid.AsString()==uid:continue
            for face,shape in node['shapes'].items():
                if item.IsOnLayer(face) and p.SHAPE.Collide(item.GetEffectiveShape(face),shape,0):faces.add(face)
        if len(faces)!=1:continue
        board.Remove(item)
        try:
            remaining=component_obligations(obligations,removed|{uid})
            require_components(remaining,_groups(copper_graph(board,nets,deadline,include_zones=False)))
        except BaseException:board.Add(item);raise
        removed.add(uid);owners.retain_native(board,item)
    return removed


def repair_saved(request,*,deadline):
    path=Path(request['board']);original=path.read_bytes();field=load(path.parent)
    if field is None:raise ValueError('Joint repair requires an existing field receipt')
    generated=request.get('generated_ids',[])
    if not isinstance(generated,list) or len(generated)>100000 or any(not isinstance(q,str) for q in generated):
        raise ValueError('Expected complete generated ownership inventory')
    board=p.LoadBoard(str(path));before=snapshot(board);verify_native(board,field,deadline=deadline)
    if any(not q.GetIsRuleArea() for q in board.Zones()):
        return NativeRepairResult({'accepted':False,'reason':'Joint repair requires a pre-zone saved board','attempts':[]}).retain_native(board)
    cfg=json.loads((path.parent/'.smilepcb-keyboard.json').read_text())
    plane_nets=set(cfg.get('plane_nets',()))
    verified=load_verified_interfaces(board,cfg,path.parent,deadline=deadline,retire_completed=True)
    from .repair_source_scope import repair_source_scope
    ports=verified['ports'] if verified else [];resolved=verified.get('resolved',()) if verified else ()
    preserved,frozen=repair_source_scope(before,ports,resolved,deadline=deadline)
    protected=set(request.get('protected_ids',()))|set(field.get('prerequisite_tracks',{}))|set(preserved)|{u for q in ports for u in q['added_ids']}
    # Pre-zone connectivity cannot prove that a future plane contact is redundant.
    protected.update(u for u,q in before['tracks'].items() if q['net'] in plane_nets)
    from .interface_launches import interface_identity
    held={interface_identity(q) for q in ports if q['net'] not in frozen and set(q['added_ids'])<=preserved.keys()}
    nets={q['net'] for q in before['tracks'].values() if q['net']}|{q['net'] for f in before['footprints'].values() for q in f['pads'] if q['net']}
    graph=copper_graph(board,nets,deadline,include_zones=False);before_groups=_groups(graph);parts=_pads(graph)
    original_nets={q['item'].m_Uuid.AsString():q['net'] for q in graph['nodes']}
    excluded=plane_nets|{q[1] for q in field['terminals'].values()}
    targets=_targets(graph,excluded,deadline);result=NativeRepairResult({'accepted':False,'attempts':[],'targets':len(targets)})
    trial=items=router=current_graph=proof=actual=tail_result=retired_owners=None
    def finish():return result.retain_native(board,graph,trial,items,router,current_graph,proof,actual,tail_result,retired_owners)
    proof_reserve=min(15,max(3,(deadline-time.monotonic())*.2));search_end=deadline-proof_reserve
    for target in targets:
        if time.monotonic()>=search_end:break
        with reserve_launches(ports,resolved=resolved,repair_reserved=held):
            plans,region=_plans(board,graph,target,field,generated,protected,min(search_end,time.monotonic()+20))
        result.setdefault('discovery',[]).append({'net':target[3],'plans':len(plans),'region':region.bounds,'search':getattr(region,'diagnostic',{})})
        result['discovery'][-1]['proposals']=[{'removed_ids':sorted(plan['removed']),
            'removed_nets':sorted({before['tracks'][u]['net'] for u in plan['removed']}),
            'cost':plan['cost'],'side':plan['side'],'layer_word':plan['layer_word'],'cut_components':plan['cut_components'],'width_nm':plan['width'],'native_min_width_nm':board.GetDesignSettings().m_TrackMinWidth,'vias':plan['vias'],'legs':plan['legs']} for plan in plans]
        contact_cache={};expansion_feedback={}
        def expand(plan,removed):
            missing=set(removed)-set(contact_cache)
            if missing and time.monotonic()<search_end:contact_cache.update(_adjacency(graph,missing,search_end))
            return expansion_masks(removed,contact_cache,plan['soft'])
        for plan,removed,reverse in trial_schedule(plans,expand,feedback=lambda plan,removed:expansion_feedback.get((id(plan),removed))):
            if time.monotonic()>=search_end:break
            row={'removed_ids':sorted(removed),'target_net':plan['net'],'accepted':False,'reverse_order':reverse,'stage':'derive','steps':[]}
            result['attempts'].append(row);feedback={}
            row['feedback_trial']=any(key[0]==id(plan) and mask==removed for key,mask in expansion_feedback.items())
            try:
                trial_region,region_diagnostic=_restoration_region(graph,removed,plan,ports,contact_cache,search_end)
                row['restoration_region']=region_diagnostic
                retired_owners=NativeRepairResult()
                trial=p.LoadBoard(str(path));retained=set(before['tracks'])-removed;physical_before=_physical(board,retained)
                # Artwork attachments are outside copper_graph. Refuse
                # affected artwork rather than silently omit it.
                from smilepcb.copper_obstacles import copper_graphics
                arts=list(copper_graphics(board))
                items={q.m_Uuid.AsString():q for q in trial.GetTracks()}
                for uid in removed:
                    item=items[uid]
                    if any(net==item.GetNetname() and item.IsOnLayer(face) and p.SHAPE.Collide(item.GetEffectiveShape(face),shape,0) for _,shape,net,face in arts):raise ValueError('Removed copper touches unsupported artwork')
                    trial.Remove(item)
                obligations=component_obligations(before_groups,removed)
                target_groups=[q for q in obligations if q&plan['target_pads']]
                if len(target_groups)!=2:raise ValueError('Target no longer describes two original components')
                obligations=[q for q in obligations if not q&plan['target_pads']]+[frozenset().union(*target_groups)]
                with reserve_launches(ports,resolved=resolved,repair_reserved=held):
                    router=_router(trial,trial_region,plan['width'],search_end);router.refresh_interfaces(copper_graph(trial,nets,search_end,include_zones=False))
                    row['stage']='target';_emit(router,plan)
                    row['stage']='restore'
                    roles,steps=_restore(trial,obligations,original_nets,set(removed),before,field,trial_region,nets,search_end,reverse,steps=row['steps'],
                        movable=plan['soft'] if removed==plan['removed'] and not reverse else (),feedback=feedback)
                    row['stage']='hygiene'
                    affected_nets={before['tracks'][u]['net'] for u in removed}|{plan['net']}
                    via_candidates={u for u in set(generated)|set(field['tracks']) if u in before['tracks'] and before['tracks'][u]['net'] in affected_nets
                        and field.get('roles',{}).get(u,'').endswith('_access')
                        and u not in field.get('prerequisite_tracks',{})
                        and trial_region.contains(tuple(before['tracks'][u].get('position',before['tracks'][u].get('start',(0,0)))))}-set(removed)
                    obsolete=_retire_single_layer_vias(trial,via_candidates,protected,obligations,nets,search_end,retired_owners)
                    removed=set(removed)|obsolete;obligations=component_obligations(obligations,obsolete)
                    from .joint_tail_hygiene import normalize_generated_tails
                    live_ids={q.m_Uuid.AsString() for q in trial.GetTracks()}
                    tail_protected=(protected|set(field['tracks'])|set(field.get('prerequisite_tracks',{}))|(live_ids-set(before['tracks'])))&live_ids
                    tail_result=normalize_generated_tails(trial,generated_ids=generated,protected_ids=tail_protected,deadline=search_end)
                    row['tail_hygiene']=dict(tail_result)
                    removed.update(tail_result['removed_ids'])
                    obligations=component_obligations(obligations,tail_result['removed_ids'])
                    row['stage']='proof'
                    after=snapshot(trial);added=set(after['tracks'])-set(before['tracks'])
                    current_graph=copper_graph(trial,nets,deadline,include_zones=False)
                    require_components(obligations,_groups(current_graph),added=added)
                    require_partitions(parts,_pads(current_graph),plan['target_pads'])
                    if _physical(board,set(before['tracks'])-removed)!=_physical(trial,set(before['tracks'])-removed):raise ValueError('Retained physical properties changed')
                    contract=replace_field(before,after,field,removed=removed,generated_ids=generated,protected_ids=protected,new_roles=roles)
                    proof=FrozenProofRouter(trial,deadline=deadline,track_width_nm=plan['width'],graph=current_graph,covered_nets=nets)
                    actual={q.m_Uuid.AsString():q for q in trial.GetTracks()}
                    for uid in sorted(added):
                        _check(deadline);item=actual[uid]
                        legal=saved_via_clear_cached(proof,item) if isinstance(item,p.PCB_VIA) else proof.clear(p.SHAPE_SEGMENT(item.GetStart(),item.GetEnd(),item.GetWidth()),item.GetLayer(),item.GetNetname())
                        if not legal:raise ValueError('New copper failed full-union creation proof')
                    proof.assert_unchanged();verify_native(trial,contract,deadline=deadline)
                    load_verified_interfaces(trial,cfg,path.parent,deadline=deadline,retire_completed=True)
                    row['stage']='export'
                    with tempfile.TemporaryDirectory(prefix='.smilepcb-joint-',dir=path.parent) as temporary:
                        exported=Path(temporary)/'candidate.kicad_pcb';seal=_seal(trial)
                        p.PCB_IO_KICAD_SEXPR().SaveBoard(str(exported),trial)
                        if _seal(trial)!=seal:raise ValueError('Native export changed accepted world')
                        composed=splice_copper(original.decode(),removed,exported.read_text(),added,endpoint_replacements=tail_result['endpoint_replacements'])
                        def saved_proof():
                            _check(deadline);saved=p.LoadBoard(str(path))
                            if snapshot(saved)!=after or _physical(saved)!=_physical(trial):raise ValueError('Saved reload changed native world')
                            saved_graph=copper_graph(saved,nets,deadline,include_zones=False)
                            require_components(obligations,_groups(saved_graph),added=added)
                            require_partitions(parts,_pads(saved_graph),plan['target_pads'])
                            verify_native(saved,contract,deadline=deadline)
                            load_verified_interfaces(saved,cfg,path.parent,deadline=deadline,retire_completed=True)
                            result.retain_native(saved,saved_graph)
                        publish_verified(path,path.parent/MARKER,composed.encode(),contract,saved_proof)
                    row.update(accepted=True,removed_ids=sorted(removed),added_ids=sorted(added))
                    pad_nets={}
                    for i,node in enumerate(current_graph['nodes']):
                        if isinstance(node['item'],p.PAD) and node['item'].GetNumber():pad_nets.setdefault(node['net'],set()).add(current_graph['find'](i))
                    gaps={net:len(roots)-1 for net,roots in pad_nets.items() if len(roots)>1}
                    result.update(accepted=True,pad_group_gaps=sum(gaps.values()),pad_groups_missing=gaps,added_ids=sorted(added),removed_ids=sorted(removed),
                        joint_field_transition={'removed_ids':sorted(removed),'new_roles':roles},
                        proof={'full_conductor_components':True,'all_other_pad_partitions':True,'full_union_clearance':True,'saved_reload':True,'retained_raw_nodes':True})
                    return finish()
            except (ValueError,TimeoutError) as error:
                row['reason']=str(error)
                if feedback.get('removed_ids'):
                    grown=frozenset(removed)|frozenset(feedback['removed_ids'])
                    try:
                        if not set(removed)<grown or len(grown)>16:raise ValueError('Unbounded blocker feedback')
                        component_obligations(before_groups,grown)
                    except ValueError as rejected:row['feedback_rejection']=str(rejected)
                    else:
                        expansion_feedback[(id(plan),frozenset(removed))]=grown
                        row['feedback_removed_ids']=sorted(grown)
    result['reason']='No bounded joint replacement passed complete native proof'
    return finish()
