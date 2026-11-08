"""Incremental native controller outlets with transactional owner rerouting.

The chosen short launch neck is a conservative search aid, never a capacity
proof. Every selected physical source remains mandatory, including sources
with no straight neck. Only copper created by this invocation may be removed.
"""
from collections import Counter, deque
import math
import time

from smilepcb.native_worker import p, snapshot
from .bank_geometry import clean_port_path, port_outside_bounds
from .grid_search import search_boundary
from .interface_launches import current_launches, reserve_launches
from .local_router import LocalRouter, vec, xy
from .native_graph import copper_graph
from .safety import require_via_land_clearance


class _Region:
    def __init__(self,bounds):self.bounds=bounds

    def accepts_path(self,points):
        if len(points)<2 or all(q==points[0] for q in points):return True
        l,t,r,b=self.bounds
        return (clean_port_path(points) is not None and
                all(l-1000000<=q[0]<=r+1000000 and t-1000000<=q[1]<=b+1000000 for q in points))


class _EscapeRouter(LocalRouter):
    def __init__(self,board,deadline,*,bounds,necks,routed,clearance_nm,search_budget_s):
        super().__init__(board,deadline=deadline,compact=True,
                         track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000,
                         path_policy=_Region(bounds))
        self.minimum_clearance=max(self.minimum_clearance,clearance_nm)
        self.net_clearances={n:max(gap,clearance_nm) for n,gap in self.net_clearances.items()}
        self.bounds=bounds;self.necks=necks;self.routed=routed;self.hits=Counter()
        self.grid_step=125000;self.search_budget_s=search_budget_s
        self.search_expansion_limit=40000;self.via_cost_nm=1000000
        self.layer_change_heuristic_nm=0;self.heuristic_weight=1

    def collisions(self,shape,layer,net,via=False):
        for neck in self.necks:
            if neck['net']==net or neck['owner'] in self.routed or neck['layer']!=layer:continue
            gap=max(self.minimum_clearance,self.net_clearances.get(net,0),self.net_clearances.get(neck['net'],0))
            if p.SHAPE.Collide(shape,neck['shape'],gap):
                uid='neck:'+neck['owner']+':'+neck['direction'];self.hits[uid]+=1;yield uid
        for uid in super().collisions(shape,layer,net,via):
            self.hits[uid]+=1
            if uid in ('controller-first-contact','reserved-controller-launch') and self.port_policy is not None:
                # Preserve the native policy's admission. Attribute its exact
                # foreign continuation hits so a movable owner can be retried.
                policy=self.port_policy
                for index in policy.launch_indices:
                    port=policy.ports[index]
                    if port['net']==net or port['exit_layer']!=layer:continue
                    shape2=_continuation(port,self.track_width)
                    gap=max(policy.launch_gap,policy.net_clearances.get(net,policy.launch_gap),
                            policy.net_clearances.get(port['net'],policy.launch_gap))
                    if p.SHAPE.Collide(shape,shape2,gap):self.hits[port['terminal_id']]+=1
            yield uid

    def via_clear(self,position,net):
        l,t,r,b=self.bounds;inset=self.via_width//2
        if not (l-1000000+inset<=position[0]<=r+1000000-inset and
                t-1000000+inset<=position[1]<=b+1000000-inset):return False
        # A reserved future trace must clear both the annulus (via collisions
        # on both faces below) and the hole under the live native hole rule.
        hole=p.SHAPE_CIRCLE(vec(position),self.drill//2)
        for neck in self.necks:
            if neck['net']==net or neck['owner'] in self.routed:continue
            if p.SHAPE.Collide(hole,neck['shape'],self.native_hole_clearance+1000):
                self.hits['neck:'+neck['owner']+':'+neck['direction']]+=1;return False
        return super().via_clear(position,net)


def _history_penalty(records,via_cost_nm):
    """One finite reuse cost, scoped to the displaced physical owner."""
    def cost(shape,layer,net,via):
        if not any(face==layer and p.SHAPE.Collide(shape,old,0) for face,old in records):return 0
        if via:return via_cost_nm
        segment=shape.GetSeg();a,b=xy(segment.A),xy(segment.B)
        return math.hypot(b[0]-a[0],b[1]-a[1])
    return cost


def _via_phase_candidates(world,group,hits,*,admissible=None):
    """Obstacle events propose phases only; Native admits their vias and leads."""
    from .controller_phase_geometry import iter_via_phase_candidates
    selected={uid for uid,count in hits.items() if count>0}|set(group['pad_ids'])
    records=set();net=group['net'];profile=world.generation
    def add(shape,gap):
        box=shape.BBox()
        records.add(((box.GetLeft(),box.GetTop(),box.GetRight(),box.GetBottom()),gap))
    for table in world.buckets.values():
        for rows in table.values():
            if time.monotonic()>=world.deadline:return []
            for uid,shape,othernet,gap in rows:
                if uid in selected and othernet!=net:
                    add(shape,max(gap,world.net_clearances.get(net,0),profile.foreign_copper_clearance_nm))
    land_gap=max(profile.via_to_smd_clearance_nm,100000-(world.via_width-world.drill)//2,0)
    for rows in world.solder_lands.values():
        if time.monotonic()>=world.deadline:return []
        for uid,shape in rows:
            if uid in selected:add(shape,land_gap)
    for rows in world.holes.values():
        if time.monotonic()>=world.deadline:return []
        for uid,shape,_ in rows:
            if uid in selected:add(shape,max(world.hole_clearances[uid],profile.via_to_hole_clearance_nm))
    for pad in group['pads']:
        if time.monotonic()>=world.deadline:return []
        for face in (p.F_Cu,p.B_Cu):
            if pad.IsOnLayer(face):
                add(pad.GetEffectiveShape(face),land_gap if pad.GetAttribute()==p.PAD_ATTRIB_SMD else 0)
    l,t,r,b=group['bounds'];radius=world.via_width//2
    bounds=(l-1000000+radius,t-1000000+radius,r+1000000-radius,b+1000000-radius)
    return iter_via_phase_candidates([dict(box_nm=box,clearance_nm=gap) for box,gap in sorted(records)],
        [xy(pad.GetPosition()) for pad in group['pads']],via_width_nm=world.via_width,
        grid_step_nm=world.grid_step,bounds_nm=bounds,admissible=admissible)


def _continuation(port,width):
    end=tuple(port['exit_nm']);origin=tuple(port['heading_origin_nm'])
    h=tuple((end[i]>origin[i])-(end[i]<origin[i]) for i in (0,1))
    if h not in ((1,0),(-1,0),(0,1),(0,-1)):raise ValueError('Non-cardinal controller departure')
    future=tuple(end[i]+h[i]*500000 for i in (0,1))
    return p.SHAPE_SEGMENT(vec(end),vec(future),width)


def _pad_key(pad):
    number=pad.GetNumber()
    return (pad.GetParentFootprint().GetReference(),(0,int(number)) if number.isdecimal() else (1,number),
            xy(pad.GetPosition()),pad.m_Uuid.AsString())


def _inventory(board,refs,mcus,nets,frames,deadline):
    fps={f.GetReference():f for f in board.GetFootprints()}
    if not mcus or not mcus<=refs or not refs<=fps.keys():
        raise ValueError('Controller escapes require existing MCU/controller references')
    if frames is not None and (not isinstance(frames,dict) or set(frames)-mcus):
        raise ValueError('Controller frames require MCU references')
    boxes={}
    for ref in mcus:
        pads=list(fps[ref].Pads())
        shapes=[d.GetEffectiveShape(face).BBox() for d in pads for face in (p.F_Cu,p.B_Cu) if d.IsOnLayer(face)]
        if not shapes:raise ValueError('Controller has no physical copper pads')
        copper=(min(b.GetLeft() for b in shapes),min(b.GetTop() for b in shapes),
                max(b.GetRight() for b in shapes),max(b.GetBottom() for b in shapes))
        # A caller may supply a fixed pad-enclosing frame. Otherwise derive a
        # local frame from the actual package, independent of board coordinates.
        box=frames.get(ref) if frames is not None else None
        if box is None:box=tuple(v+(-1000000 if i<2 else 1000000) for i,v in enumerate(copper))
        if (not isinstance(box,(list,tuple)) or len(box)!=4 or any(type(v) is not int for v in box) or
                box[0]>copper[0] or box[1]>copper[1] or box[2]<copper[2] or box[3]<copper[3]):
            raise ValueError('Controller frame must enclose actual pad copper')
        boxes[ref]=tuple(box)
    graph=copper_graph(board,nets,deadline,include_zones=False);groups={}
    selected={d.m_Uuid.AsString() for ref in mcus for d in fps[ref].Pads() if d.GetNumber() and d.GetNetname() in nets}
    found=set()
    for indices in graph['groups'].values():
        pads=sorted((graph['nodes'][i]['item'] for i in indices
                     if graph['nodes'][i]['item'].m_Uuid.AsString() in selected),key=_pad_key)
        if not pads:continue
        ids=[d.m_Uuid.AsString() for d in pads];found.update(ids);pad=pads[0]
        ref=pad.GetParentFootprint().GetReference();a=xy(pad.GetPosition());center=xy(fps[ref].GetPosition())
        axis=0 if abs(a[0]-center[0])>abs(a[1]-center[1]) else 1;sign=1 if a[axis]>=center[axis] else -1
        group_boxes=[boxes[d.GetParentFootprint().GetReference()] for d in pads]
        bounds=tuple((min if i<2 else max)(box[i] for box in group_boxes) for i in range(4))
        owner=min(ids)
        groups[owner]={'net':pad.GetNetname(),'pads':pads,'pad_ids':ids,'anchor':pad,
                       'bank':(ref,axis,sign),'bounds':bounds}
    if found!=selected or {g['net'] for g in groups.values()}!=nets:
        raise ValueError('Controller escape selection omits physical source obligations')
    return groups


def _launches(world,groups,deadline):
    """Prioritize scarce launches and hold one inward-or-sole native corridor."""
    necks=[];scarcity={}
    for owner,group in groups.items():
        if time.monotonic()>=deadline:raise TimeoutError('Controller launch inventory deadline')
        pad=group['anchor'];a=xy(pad.GetPosition());_,axis,sign=group['bank'];net=group['net']
        faces=[face for face in (p.F_Cu,p.B_Cu) if pad.IsOnLayer(face)]
        count=0;alternatives=[]
        for face in faces:
            for length in range(250000,1500001,125000):
                z=list(a);z[axis]+=sign*length
                count+=world.clear(p.SHAPE_SEGMENT(vec(a),vec(z),world.track_width),face,net)
            box=pad.GetEffectiveShape(face).BBox()
            gap=max(world.minimum_clearance,world.net_clearances.get(net,0))
            for direction in (sign,-sign):
                edge=(box.GetRight() if axis==0 else box.GetBottom()) if direction>0 else (box.GetLeft() if axis==0 else box.GetTop())
                z=list(a);z[axis]=edge+direction*(gap+world.track_width//2+1000);z=tuple(z)
                shape=p.SHAPE_SEGMENT(vec(a),vec(z),world.track_width)
                if world.clear(shape,face,net):
                    alternatives.append({'owner':owner,'net':net,'direction':'outward' if direction==sign else 'inward',
                        'layer':face,'path_nm':[a,z],'pad_id':pad.m_Uuid.AsString(),'shape':shape})
        # The alternative restriction may miss a feasible joint routing. Zero
        # alternatives does not drop this source from the boundary search.
        chosen=next((q for q in alternatives if q['direction']=='inward'),next(iter(alternatives),None))
        if chosen is not None:necks.append(chosen)
        scarcity[owner]=(count,axis,sign,a[1-axis],_pad_key(pad))
    return necks,scarcity


def _receipt(board,group,world):
    layer,path=world.last_plan['legs'][-1]
    if len(path)<2:raise ValueError('Controller outlet has no terminal track')
    origin,end=tuple(path[-2]),tuple(path[-1]);h=tuple((end[i]>origin[i])-(end[i]<origin[i]) for i in (0,1))
    if not port_outside_bounds(end,h,group['bounds'],world.track_width):raise ValueError('Controller outlet remains inside its frame')
    items=[t for t in board.GetTracks() if t.m_Uuid.AsString() in set(world.added)]
    terminal=[t for t in items if t.GetClass()=='PCB_TRACK' and t.GetLayer()==layer and
              {xy(t.GetStart()),xy(t.GetEnd())}=={origin,end}]
    if len(terminal)!=1:raise ValueError('Controller outlet lacks its exact terminal identity')
    vias=[t for t in items if isinstance(t,p.PCB_VIA)]
    via=next((t for t in vias if xy(t.GetPosition())==tuple(world.last_plan['vias'][-1])),None) if vias else None
    if vias and via is None:raise ValueError('Controller outlet lacks its actual plated via')
    return {'net':group['net'],'pad_ids':group['pad_ids'],'kind':'via' if via else 'same_layer',
            'via_id':via.m_Uuid.AsString() if via else None,'via_nm':xy(via.GetPosition()) if via else None,
            'exit_nm':end,'exit_layer':layer,'heading_origin_nm':origin,'terminal_id':terminal[0].m_Uuid.AsString(),
            'added_ids':list(world.added),'axis':group['bank'][1],'sign':group['bank'][2],'bank':group['bank'],'profile':None}


def _physical_proof_world(board,graph,nets,deadline,clearance_nm):
    """Seal only query-stable launch scopes; retain fresh refresh semantics otherwise."""
    from .conditional_geometry import FrozenProofRouter
    from .interface_launches import current_launch_inventory
    inventory=current_launch_inventory()
    # A resolved overlapping source may be stale after a caller rollback.
    # LocalRouter refreshes it on via queries, mutating admission state. Keep
    # the original fresh-oracle path rather than freezing that side effect.
    refresh_required=(inventory is not None and any(
        key in inventory.resolved and port['net'] in nets for key,port in inventory.ports.items()))
    options=dict(deadline=deadline,track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000)
    world=(LocalRouter(board,compact=True,**options) if refresh_required else
           FrozenProofRouter(board,graph=graph,covered_nets=nets,refresh_launches=False,**options))
    world.minimum_clearance=max(world.minimum_clearance,clearance_nm)
    world.net_clearances={n:max(g,clearance_nm) for n,g in world.net_clearances.items()}
    return world


def _prove(board,before,ports,plans,fixed,deadline,clearance_nm):
    if time.monotonic()>=deadline:raise TimeoutError('Controller escape proof deadline')
    after=snapshot(board);owned=[u for q in ports.values() for u in q['added_ids']]
    if (after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=t for u,t in before['tracks'].items()) or
            len(set(owned))!=len(owned) or set(after['tracks'])-set(before['tracks'])!=set(owned)):
        raise ValueError('Controller escape changed retained geometry or ownership')
    graph=copper_graph(board,{q['net'] for q in ports.values()},deadline,include_zones=False)
    ids={node['item'].m_Uuid.AsString():i for i,node in enumerate(graph['nodes'])}
    for port in ports.values():
        required=port['pad_ids']+port['added_ids']
        if any(u not in ids for u in required) or len({graph['find'](ids[u]) for u in required})!=1:
            raise ValueError('Controller escape lacks actual source-to-terminal contact')
    require_via_land_clearance(board,deadline=deadline)
    from .conditional_geometry import FrozenProofRouter
    world=_physical_proof_world(board,graph,{q['net'] for q in ports.values()},deadline,clearance_nm)
    for owner,port in ports.items():
        if time.monotonic()>=deadline:raise TimeoutError('Controller escape geometry proof deadline')
        plan=plans[owner];net=port['net']
        for face,path in plan['legs']:
            if len(path)>1 and clean_port_path(path) is None:raise ValueError('Controller escape has invalid native turns')
            if any(not world.clear(p.SHAPE_SEGMENT(vec(a),vec(z),world.track_width),face,net) for a,z in zip(path,path[1:])):
                raise ValueError('Controller escape geometry failed native clearance')
        if plan['vias']:
            if isinstance(world,FrozenProofRouter):
                with world.excluding_copper(port['added_ids'],clearance_nm=clearance_nm):
                    if any(not world.via_clear(v,net) for v in plan['vias']):raise ValueError('Controller escape via failed native clearance')
            else:
                oracle=LocalRouter(board,deadline=deadline,compact=True,track_width_nm=world.track_width,ignored_copper=port['added_ids'])
                oracle.minimum_clearance=max(oracle.minimum_clearance,clearance_nm)
                oracle.net_clearances={n:max(g,clearance_nm) for n,g in oracle.net_clearances.items()}
                if any(not oracle.via_clear(v,net) for v in plan['vias']):raise ValueError('Controller escape via failed native clearance')
    if isinstance(world,FrozenProofRouter):world.assert_unchanged()
    all_ports=list(ports.values())+list(fixed)
    # Validate newly generated heads under the same first-contact policy as
    # retained heads. Scope only the continuation stage: the existing owned
    # tracks themselves are not new consumer connections to their terminals.
    with reserve_launches(all_ports):
        consumer=LocalRouter(board,deadline=deadline,compact=True)
        consumer.minimum_clearance=max(consumer.minimum_clearance,clearance_nm)
        consumer.net_clearances={n:max(g,clearance_nm) for n,g in consumer.net_clearances.items()}
        continuations=[]
        for port in all_ports:
            if time.monotonic()>=deadline:raise TimeoutError('Controller continuation proof deadline')
            shape=_continuation(port,consumer.track_width);net=port['net'];face=port['exit_layer']
            if not consumer.clear(shape,face,net):raise ValueError('Controller continuation lost native clearance')
            for other,layer,previous in continuations:
                gap=max(consumer.minimum_clearance,consumer.net_clearances.get(net,0),consumer.net_clearances.get(other,0))
                if net!=other and face==layer and p.SHAPE.Collide(shape,previous,gap):
                    raise ValueError('Controller continuations overlap')
            continuations.append((net,face,shape))
    if time.monotonic()>=deadline:raise TimeoutError('Controller escape final deadline')


def route_controller_escapes(board,*,controller_refs,mcu_refs,include_nets,exit_bounds_nm=None,
                             deadline,clearance_nm=173000,search_budget_s=10,max_ripups=8):
    """Route every selected source group or restore the exact entry board.

    The deadline includes final native proof; retries never replenish it.
    Expected search/proof rejection returns a receipt. Unexpected exceptions
    propagate only after the same exact rollback as ordinary rejection.
    """
    if (not isinstance(include_nets,(list,tuple,set,frozenset)) or not include_nets or
            any(type(n) is not str or not n for n in include_nets)):
        raise ValueError('Controller escape requires explicit nonempty signal nets')
    if (type(clearance_nm) is not int or not max(150000,board.GetDesignSettings().m_MinClearance)<=clearance_nm<=500000 or
            type(search_budget_s) not in (int,float) or not math.isfinite(search_budget_s) or not 0<search_budget_s<=10 or
            type(max_ripups) is not int or not 0<=max_ripups<=8 or not math.isfinite(deadline)):
        raise ValueError('Unsupported bounded controller escape limits')
    if any(not z.GetIsRuleArea() for z in board.Zones()):raise ValueError('Controller escapes require a board without plane fills')
    before=snapshot(board);started=time.monotonic();ports={};plans={};routed=set();events=[];history={}
    result={'accepted':False,'ports':[],'added_ids':[],'selected_necks':[],'events':events,
            'algorithm':'native-boundary-preferred-inward-neck','reservation_limitation':'A chosen neck is a conservative search restriction, not an infeasibility proof.'}
    try:
        if deadline<=started:raise TimeoutError('Controller escape deadline expired')
        groups=_inventory(board,set(controller_refs),set(mcu_refs),set(include_nets),exit_bounds_nm,deadline)
        fixed=tuple(current_launches());frames=[g['bounds'] for g in groups.values()]
        all_bounds=tuple((min if i<2 else max)(b[i] for b in frames) for i in range(4))
        # Reserve proof within the caller's absolute budget, including on tiny
        # tails. No validation or retry may extend this phase's deadline.
        routing_stop=deadline-5
        world=_EscapeRouter(board,deadline=routing_stop,bounds=all_bounds,necks=(),routed=routed,
                            clearance_nm=clearance_nm,search_budget_s=search_budget_s)
        necks,scarcity=_launches(world,groups,routing_stop)
        result['selected_necks']=[{k:v for k,v in q.items() if k!='shape'} for q in necks]
        result.update(groups=len(groups),generation_profile=world.generation.name,track_width_nm=world.track_width,
                      effective_clearance_nm=world.minimum_clearance,net_clearances_nm=dict(world.net_clearances))
        pending=deque(sorted(groups,key=lambda key:scarcity[key]));attempts=Counter();pairs=Counter();ripups=0
        while pending:
            if time.monotonic()>=routing_stop:raise TimeoutError('Controller escape shared routing deadline')
            owner=pending.popleft();group=groups[owner];attempts[owner]+=1
            with reserve_launches(fixed+tuple(ports.values())):
                owner_search_cutoff=min(routing_stop,time.monotonic()+search_budget_s)
                world=_EscapeRouter(board,deadline=owner_search_cutoff,bounds=group['bounds'],necks=necks,routed=routed,
                                    clearance_nm=clearance_nm,search_budget_s=search_budget_s)
                world.routing_penalty=_history_penalty(history.get(owner,()),world.via_cost_nm)
                starts=[(xy(d.GetPosition()),face) for d in group['pads'] for face in (p.F_Cu,p.B_Cu) if d.IsOnLayer(face)]
                remaining_expansions=world.search_expansion_limit;owner_hits=Counter()
                def attempt_search():
                    nonlocal remaining_expansions
                    remaining=owner_search_cutoff-time.monotonic()
                    if remaining<=0:raise TimeoutError('Controller owner search deadline exhausted')
                    world.search_budget_s=remaining;world.search_expansion_limit=remaining_expansions
                    world.hits=Counter()
                    okay=search_boundary(world,starts,group['bounds'],group['net'],
                                         continuation_width_nm=max(p.FromMM(.2),world.track_width))
                    remaining_expansions-=world.last_search['expanded'];owner_hits.update(world.hits)
                    return okay
                okay=attempt_search()
                neck_hits=[(uid,count) for uid,count in owner_hits.most_common() if uid.startswith('neck:') and count>0]
                remaining=owner_search_cutoff-time.monotonic()
                if not okay and neck_hits and remaining>0 and remaining_expansions>0:
                    # Hypothetical peer necks are a search preference. Actual
                    # copper and live port reservations still use Native checks.
                    relaxation=dict(event='relax_future_necks',owner=owner,net=group['net'],attempt=attempts[owner],
                        neck_hits=neck_hits,initial_search=world.last_search,retry_deadline=owner_search_cutoff,
                        owner_search_cutoff=owner_search_cutoff,remaining_search_s=remaining,
                        remaining_expansions=remaining_expansions)
                    events.append(relaxation)
                    world=_EscapeRouter(board,deadline=owner_search_cutoff,bounds=group['bounds'],necks=(),routed=routed,
                                        clearance_nm=clearance_nm,search_budget_s=remaining)
                    world.routing_penalty=_history_penalty(history.get(owner,()),world.via_cost_nm)
                    okay=attempt_search()
                    relaxation.update(search_succeeded=okay,search=world.last_search,collision_ids=world.hits.most_common(30))
                if (not okay and world.last_search['reason']=='frontier_exhausted' and
                        time.monotonic()<owner_search_cutoff and remaining_expansions>0):
                    initial_search=world.last_search
                    summary=dict(event='adaptive_grid_phases',owner=owner,net=group['net'],
                        attempted=0,via_rejected=0,deferred_without_direct_lead=0,candidate_count=0,enumeration_complete=False,
                        scope='finite obstacle-event phases; exhaustion is not physical infeasibility')
                    events.append(summary);deferred=[]
                    def admissible(point):
                        if time.monotonic()>=owner_search_cutoff or remaining_expansions<=0:
                            raise TimeoutError('Controller owner phase allowance exhausted')
                        world.hits=Counter()
                        via_ok=world.via_clear(point,group['net']);owner_hits.update(world.hits)
                        if time.monotonic()>=owner_search_cutoff:
                            raise TimeoutError('Controller owner phase deadline exhausted')
                        if not via_ok:summary['via_rejected']+=1;return False
                        world.hits=Counter()
                        lead=next((path for source,face in starts
                            if (path:=world.leg(source,point,face,group['net'])) is not None),None)
                        owner_hits.update(world.hits)
                        if time.monotonic()>=owner_search_cutoff:
                            raise TimeoutError('Controller owner phase deadline exhausted')
                        if lead is None:
                            summary['deferred_without_direct_lead']+=1;deferred.append(point);return False
                        return True
                    def phase_candidates():
                        yield from _via_phase_candidates(world,group,owner_hits,admissible=admissible)
                        yield from deferred
                    candidates=phase_candidates()
                    origin=starts[0][0];step=world.grid_step
                    seen_phases={(origin[0]%step,origin[1]%step)}
                    for point in candidates:
                        summary['candidate_count']+=1
                        if (okay or world.last_search['reason']!='frontier_exhausted' or
                                time.monotonic()>=owner_search_cutoff or remaining_expansions<=0):break
                        phase=(point[0]%step,point[1]%step)
                        if phase in seen_phases:continue
                        seen_phases.add(phase)
                        world.grid_origin_nm=point
                        phase_event=dict(event='adaptive_grid_phase',owner=owner,net=group['net'],attempt=attempts[owner],
                            grid_origin_nm=list(point),initial_search=initial_search,owner_search_cutoff=owner_search_cutoff,
                            remaining_search_s=owner_search_cutoff-time.monotonic(),remaining_expansions=remaining_expansions)
                        events.append(phase_event);summary['attempted']+=1
                        okay=attempt_search()
                        phase_event.update(search_succeeded=okay,search=world.last_search,collision_ids=world.hits.most_common(30))
                        if (okay or world.last_search['reason']!='frontier_exhausted' or
                                time.monotonic()>=owner_search_cutoff or remaining_expansions<=0):break
                    else:summary['enumeration_complete']=True
                    summary.update(search_succeeded=okay,remaining_expansions=remaining_expansions,
                        owner_deadline_exhausted=time.monotonic()>=owner_search_cutoff)
                if not okay:world.hits=owner_hits
                if okay:
                    ports[owner]=_receipt(board,group,world);plans[owner]=world.last_plan;routed.add(owner)
                    events.append({'event':'routed','net':group['net'],'owner':owner,'complete':len(ports),'search':world.last_search})
                    continue
            owners={u:key for key,q in ports.items() for u in q['added_ids']};counts=Counter()
            for uid,count in world.hits.items():
                if uid in owners:counts[owners[uid]]+=count
            events.append({'event':'blocked','net':group['net'],'owner':owner,'search':world.last_search,
                           'collision_ids':world.hits.most_common(30),'movable_owners':counts.most_common()})
            movable=[key for key in counts if pairs[owner,key]<2]
            if not movable or ripups>=max_ripups or attempts[owner]>=4:
                raise ValueError('No admissible owned controller reroute' if not movable else 'Controller reroute budget exhausted')
            victim=max(movable,key=lambda key:(counts[key],groups[key]['net'],key));removed=set(ports[victim]['added_ids'])
            if removed & before['tracks'].keys():raise ValueError('Controller reroute claimed retained copper')
            remembered=[]
            for track in list(board.GetTracks()):
                uid=track.m_Uuid.AsString()
                if uid not in removed:continue
                # Remember only this victim's owned objects actually hit by
                # the blocked search, never every pass or retained source.
                if world.hits.get(uid,0)>0:
                    if isinstance(track,p.PCB_VIA):
                        remembered.extend((face,p.SHAPE_CIRCLE(vec(xy(track.GetPosition())),track.GetWidth()//2))
                                          for face in (p.F_Cu,p.B_Cu))
                    else:
                        remembered.append((track.GetLayer(),p.SHAPE_SEGMENT(track.GetStart(),track.GetEnd(),track.GetWidth())))
                board.Delete(track)
            history.setdefault(victim,[]).extend(remembered)
            del ports[victim];del plans[victim];routed.remove(victim);board.BuildConnectivity()
            pairs[owner,victim]+=1;ripups+=1;pending.appendleft(victim);pending.appendleft(owner)
            events.append({'event':'ripup','owner':owner,'displaced':victim,'removed':len(removed),'history_objects':len(remembered)})
        _prove(board,before,ports,plans,fixed,deadline,clearance_nm)
        result.update(accepted=True,ports=list(ports.values()),added_ids=[u for q in ports.values() for u in q['added_ids']],
                      retained_exact=True,ripups=ripups)
    except (ValueError,TimeoutError) as error:
        result.update(reason=str(error),routed_before_rollback=len(ports))
    finally:
        if not result['accepted']:
            for track in list(board.GetTracks()):
                if track.m_Uuid.AsString() not in before['tracks']:board.Delete(track)
            board.BuildConnectivity()
            if snapshot(board)!=before:raise ValueError('Controller escape rollback changed retained geometry')
            result['rollback_exact']=True
        result['elapsed_s']=time.monotonic()-started
    return result
