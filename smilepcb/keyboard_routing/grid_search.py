"""Bounded two-layer A* fallback using the native collision oracle."""
import heapq
import itertools
import math
import time
from smilepcb.native_worker import p
from .local_router import vec,distance
from smilepcb.routing_quality import current_quality,heading,path_cost,search_state


def planned_vias_clear(router,a,b):
    """Apply the captured creation profile to vias not on the board yet."""
    profile=getattr(router,'generation',None)
    return profile is None or profile.name=='native' or router.via_pair_clear(a,b)


def valid_plan(router,legs,vias,net):
    """Check the reconstructed plan before committing any of its copper."""
    spacing=max(p.FromMM(.61),router.drill+p.FromMM(.26))
    return (all(router.via_clear(v,net) for v in vias)
            and all(distance(a,b)>=spacing and planned_vias_clear(router,a,b)
                    for i,a in enumerate(vias) for b in vias[i+1:])
            and all(router.clear(p.SHAPE_SEGMENT(vec(a),vec(b),getattr(router,'track_width',p.FromMM(.2))),layer,net)
                    for layer,points in legs for a,b in zip(points,points[1:])))


def search(router,a,b,start_layers,end_layers,net):
    return search_many(router,[(a,l) for l in start_layers],[(b,l) for l in end_layers],net)


def search_many(router,starts,targets,net):
    if not starts or not targets or len(starts)>128 or len(targets)>128:
        raise ValueError('Expected bounded component terminals')
    quality=getattr(router,'quality',current_quality())
    # Fine enough for 0.2 mm routes, bounded independently of board dimensions.
    step=getattr(router,'grid_step',250000);budget=getattr(router,'search_budget_s',5)
    if type(budget) not in (int,float) or not 0<budget<=30 or not math.isfinite(budget):
        raise ValueError('Grid search budget must be positive and at most 30 seconds')
    limit=getattr(router,'search_expansion_limit',200000)
    if type(limit) is not int or not 1<=limit<=200000:
        raise ValueError('Grid search expansion limit must be between 1 and 200000')
    via_cost=getattr(router,'via_cost_nm',5000000)
    layer_estimate=getattr(router,'layer_change_heuristic_nm',via_cost)
    if (type(via_cost) is not int or not 0<via_cost<=100000000 or
            type(layer_estimate) is not int or not 0<=layer_estimate<=via_cost):
        raise ValueError('Expected positive bounded via cost and a compatible layer heuristic')
    stop=min(float('inf') if router.deadline is None else router.deadline,time.monotonic()+budget)
    count=itertools.count();queue=[];costs={};records={};labels={};edge_cache={};via_cache={};penalty_cache={};completions={}
    avoidance=getattr(router,'routing_penalty',None)
    spacing=max(p.FromMM(.61),router.drill+p.FromMM(.26))
    # Align the lattice with an actual constrained terminal. An otherwise
    # legal long pin corridor can fall entirely between global grid lines;
    # exact short access leads alone cannot enter it at a distant grid point.
    origin=(targets if len(targets)<len(starts) else starts)[0][0]
    def pos(state):return (state[0]*step+origin[0],state[1]*step+origin[1])
    def heuristic(state):
        return min(distance(pos(state),b)+(0 if state[2]==layer else layer_estimate) for b,layer in targets)
    octile_only=getattr(getattr(router,'generation',None),'name',None)=='dsn-safe-v1'
    def edge_path(x,y,layer):
        key=(x,y,layer)
        if key not in edge_cache:
            reverse=(y,x,layer)
            if reverse in edge_cache:
                path=edge_cache[reverse]
                edge_cache[key]=None if path is None else list(reversed(path))
            else:
                dx,dy=y[0]-x[0],y[1]-x[1]
                candidates=[[x,y]]
                if octile_only and dx and dy and abs(dx)!=abs(dy):
                    m=min(abs(dx),abs(dy));sx=1 if dx>0 else -1;sy=1 if dy>0 else -1
                    candidates=[[x,(x[0]+sx*m,x[1]+sy*m),y],
                                [x,(y[0]-sx*m,y[1]-sy*m),y],
                                [x,(x[0],y[1]),y],[x,(y[0],x[1]),y]]
                edge_cache[key]=None
                for path in sorted(candidates,key=lambda pts:path_cost(pts,profile=quality)):
                    if time.monotonic()>=stop:break
                    if all(router.clear(p.SHAPE_SEGMENT(vec(a),vec(b),getattr(router,'track_width',p.FromMM(.2))),layer,net)
                           for a,b in zip(path,path[1:])):
                        edge_cache[key]=path;break
        return edge_cache[key]
    def clear_edge(x,y,layer):return edge_path(x,y,layer) is not None
    def edge_cost(x,y,layer,incoming=None):
        path=edge_path(x,y,layer)
        return path_cost(path,initial_heading=incoming,profile=quality)+sum(penalty(a,b,layer) for a,b in zip(path,path[1:]))
    def arrival_heading(x,y,layer):
        path=edge_path(x,y,layer)
        return heading(path[-2],path[-1])
    def penalty(x,y,layer,via=False):
        if avoidance is None:return 0
        key=(x,y,layer,via) if x<=y else (y,x,layer,via)
        if key not in penalty_cache:
            shape=(p.SHAPE_CIRCLE(vec(x),router.via_width//2) if via else
                   p.SHAPE_SEGMENT(vec(x),vec(y),getattr(router,'track_width',p.FromMM(.2))))
            penalty_cache[key]=avoidance(shape,layer,net,via)
        return penalty_cache[key]
    def enqueue(state,cost,parent,history,origin=None):
        # Via spacing depends on the visited via set. A cheaper arrival can
        # dominate another only when it imposes no additional restrictions.
        history=frozenset(history)
        if any(c<=cost and h<=history for h,c in labels.get(state,[])):return
        labels[state]=[(h,c) for h,c in labels.get(state,[]) if not (cost<=c and history<=h)]
        labels[state].append((history,cost))
        # A finite multi-label beam bounds the history dimension as well as
        # expansions. This is a heuristic router, not an exhaustive proof.
        labels[state]=sorted(labels[state],key=lambda v:(v[1],len(v[0])))[:4]
        if (history,cost) not in labels[state]:return
        key=(state,history)
        if cost>=costs.get(key,float('inf')):return
        costs[key]=cost;identity=next(count)
        records[identity]=(state,parent,history,origin if parent is None else records[parent][3])
        heapq.heappush(queue,(cost+getattr(router,'heuristic_weight',1)*heuristic(state),identity,cost))
    def access_states(a,layer):
        """Check short exact leads before entering the quantized grid.

        A fine-pitch pad can sit between grid columns: nearby quantized
        segments hit its neighbors even though a straight escape past their
        ends is legal. Every extra lead uses the same collision oracle.
        """
        ix,iy=round((a[0]-origin[0])/step),round((a[1]-origin[1])/step)
        candidates={(ix+dx,iy+dy,layer) for dx in (-1,0,1) for dy in (-1,0,1)}
        for radius in (500000,1000000,2000000):
            cells=max(1,math.ceil(radius/step))
            candidates.update((ix+dx*cells,iy+dy*cells,layer) for dx,dy in
                              ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)))
        for state in sorted(candidates,key=lambda s:distance(a,pos(s))):
            if time.monotonic()>=stop:break
            if clear_edge(a,pos(state),layer):yield state
    goals={}
    for target,layer in targets:
        for state in access_states(target,layer):
            entry=edge_cost(pos(state),target,layer)
            if quality.bend_penalty_nm:
                goals.setdefault(state,[]).append((target,entry))
            elif state not in goals or entry<goals[state][0][1]:goals[state]=[(target,entry)]
    for a,layer in starts:
        for state in access_states(a,layer):
            enqueue(search_state(state,arrival_heading(a,pos(state),layer),quality),
                    edge_cost(a,pos(state),layer),None,(),a)
    end=None;expanded=0;b=None
    while queue and expanded<limit and time.monotonic()<stop:
        _,identity,cost=heapq.heappop(queue)
        if identity in completions:
            parent,target,base=completions[identity];state,_,history,_=records[parent]
            if base!=costs[(state,history)] or (history,base) not in labels[state]:continue
            end=parent;b=target;break
        state,_,history,_=records[identity]
        if cost!=costs[(state,history)] or (history,cost) not in labels[state]:continue
        expanded+=1;here=pos(state);layer=state[2]
        incoming=state[3] if quality.bend_penalty_nm else None
        def complete(target,entry):
            entry+=path_cost(edge_path(here,target,layer),initial_heading=incoming,profile=quality)-path_cost(edge_path(here,target,layer),profile=quality)
            done=next(count);completions[done]=(identity,target,cost)
            heapq.heappush(queue,(cost+entry,done,cost+entry))
        for target,entry in goals.get(state[:3],[]):complete(target,entry)
        for target,target_layer in targets:
            if layer==target_layer and distance(here,target)<=step*2 and clear_edge(here,target,layer):
                complete(target,edge_cost(here,target,layer))
        options=[]
        # Fine terminal access must not require thousands of empty-cell
        # expansions across a full keyboard. Extra strides remain exact
        # native-checked segments; the unit steps retain narrow detours.
        strides=sorted({1,min(4,max(1,2000000//step)),min(16,max(1,2000000//step))},reverse=True)
        for stride in strides:
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
                nxt=(state[0]+dx*stride,state[1]+dy*stride,layer);point=pos(nxt)
                if clear_edge(here,point,layer):
                    options.append((search_state(nxt,heading(here,point),quality),
                                    edge_cost(here,point,layer,incoming),history))
        if here not in via_cache:via_cache[here]=router.via_clear(here,net)
        if via_cache[here] and all(distance(here,old)>=spacing and planned_vias_clear(router,here,old) for old in history):
            other=p.B_Cu if layer==p.F_Cu else p.F_Cu
            via_penalty=sum(penalty(here,here,l,True) for l in (p.F_Cu,p.B_Cu))
            options.append((search_state((state[0],state[1],other),None,quality),via_cost+via_penalty,history|{here}))
        for nxt,length,new_history in options:enqueue(nxt,cost+length,identity,new_history)
    router.last_search={'expanded':expanded,'states':len(costs),'elapsed_budget_exhausted':time.monotonic()>=stop,
                        'routing_quality':quality.name,
                        'goal_states':len(goals),'terminal_origin_nm':list(origin),
                        'reason':('search_budget' if time.monotonic()>=stop else 'expansion_budget' if expanded>=limit else 'frontier_exhausted') if end is None else 'plan_found'}
    if end is None:return False
    states=[];cursor=end;a=records[end][3]
    while cursor is not None:
        state,cursor,_,_=records[cursor];states.append(state)
    states.reverse();path=[a];layer=states[0][2]
    def compressed(points):
        result=[]
        for point in points:
            if result and point==result[-1]:continue
            if len(result)>=2:
                x,y=result[-2],result[-1]
                if (y[0]-x[0])*(point[1]-y[1])==(y[1]-x[1])*(point[0]-y[0]):result.pop()
            result.append(point)
        return result
    legs=[];vias=[]
    for state in states:
        point=pos(state)
        if point!=path[-1]:path.extend(edge_path(path[-1],point,layer)[1:])
        if state[2]!=layer:
            legs.append((layer,compressed(path)));vias.append(point)
            path=[point];layer=state[2]
    if b!=path[-1]:path.extend(edge_path(path[-1],b,layer)[1:])
    legs.append((layer,compressed(path)))
    valid=valid_plan(router,legs,vias,net)
    expired=time.monotonic()>=stop or router.expired()
    if not valid or expired:
        router.last_search['reason']='validation_budget' if expired else 'reconstruction_rejected'
        router.last_search['plan_vias']=len(vias)
        router.last_search['plan_legs']=len(legs)
        return False
    router.last_search['reason']='connected'
    mark=len(router.added)
    for layer,points in legs:router.add_path(points,layer,net)
    for point in vias:router.add_via(point,net)
    return len(router.added)>mark
