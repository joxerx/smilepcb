"""Bounded geometry-derived joint repair proposals.

The pure visibility planner uses collision identities returned by a full-world
oracle. It never infers generated ownership by subtracting protected copper.
Native entry below proves all affected conductors, not only numbered pads.
"""
import heapq
import itertools
import math
import time


from .joint_visibility import visibility_points


def _neighbors(points, targets, k=14):
    result={}
    for i,a in enumerate(points):
        order=sorted((math.dist(a,b),b,j) for j,b in enumerate(points) if j!=i)
        chosen={j for _,_,j in order[:k]};sectors={};axes={}
        for _,b,j in order:
            dx,dy=b[0]-a[0],b[1]-a[1]
            sectors.setdefault(int(math.floor((math.atan2(dy,dx)+math.pi/8)/(math.pi/4)))%8,j)
            if dx==0 or dy==0 or abs(dx)==abs(dy):axes.setdefault((0 if dx==0 else 1 if dy==0 else 2,dx>0,dy>0),j)
        chosen.update(sectors.values());chosen.update(axes.values());chosen.update(targets-{i})
        result[i]=sorted(chosen,key=lambda j:(math.dist(a,points[j]),points[j]))
    for i,values in list(result.items()):
        for j in tuple(values):
            if i not in result[j]:result[j].append(i)
    return result


def search_paths(points, starts, goals, edge, via, *, soft_ids, deadline,
                 max_results=6, max_states=12000, max_removed=16, max_vias=4,
                 via_cost=1000000, displacement_cost=10000000, via_pair=None, diagnostic=None,
                 blocker_components=None, turn_admission=None, plan_admission=None):
    """Return alternative legal geometries, retaining distinct via histories.

    edge(a,b,layer) returns path, cost, blockers or None. via(point) returns
    blockers or None. Hard collisions are never admitted by this planner.
    Multi-result searches supplied with original-component identities share
    their finite expansion budget fairly across alternating layer histories.
    Optional turn_admission(incoming, outgoing, layer) checks same-layer edge
    joins using normalized integer headings; vias reset the incoming heading.
    The edge oracle still validates each edge's internal geometry. Optional
    plan_admission(proposal) rejects complete goals before result bookkeeping.
    Both callbacks must inspect their inputs without mutating them.
    """
    if not 1<=max_results<=16 or not 1<=max_states<=40000 or not 0<=max_vias<=6:
        raise ValueError('Finite joint search limits required')
    points=sorted(set(map(tuple,points)));index={q:i for i,q in enumerate(points)}
    starts=[(index[tuple(q)],l) for q,l in starts];goals={(index[tuple(q)],l) for q,l in goals}
    if not starts or not goals:return []
    soft=frozenset(soft_ids);neighbors=_neighbors(points,{i for i,l in goals})
    diverse=blocker_components is not None and max_results>1
    component_map=blocker_components or {}
    queues={};serial=itertools.count();labels={};frontiers={};edge_cache={};via_cache={};results=[];signatures=set();mask_results={};side_results={};expanded=0
    candidates={};expanded_families={};last_family=None
    goal_faces={face for i,face in goals}
    possible_families={(face,count) for i,face in starts for count in range(max_vias+1)
                       if (face if count%2==0 else 2-face) in goal_faces}
    def layer_family(visited,vias):return (visited[0][1],len(vias))
    def layer_word(family):return tuple(family[0] if i%2==0 else 2-family[0] for i in range(family[1]+1))
    def cuts(removed):return tuple(sorted({component_map.get(uid,'item:'+uid) for uid in removed}))
    origin=tuple(sum(points[i][axis] for i,l in starts)/len(starts) for axis in (0,1))
    finish=tuple(sum(points[i][axis] for i,l in goals)/len(goals) for axis in (0,1))
    direction=(finish[0]-origin[0],finish[1]-origin[1])
    side_limit=max(1,(max_results+1)//2)
    def side_of(actions):
        vertices=[q for kind,layer,value in actions for q in (value if kind=='edge' else [value])]
        cross=max((direction[0]*(q[1]-origin[1])-direction[1]*(q[0]-origin[0]) for q in vertices),key=abs,default=0)
        return 1 if cross>0 else -1 if cross<0 else 0
    def heuristic(i):return min(math.dist(points[i],points[j]) for j,l in goals)
    def path_heading(path,last=False):
        indices=range(len(path)-2,-1,-1) if last else range(len(path)-1)
        for index in indices:
            a,b=path[index:index+2];dx,dy=b[0]-a[0],b[1]-a[1]
            scale=math.gcd(abs(dx),abs(dy))
            if scale:return (dx//scale,dy//scale)
        return None
    def arrival(actions):
        return path_heading(actions[-1][2],True) if actions and actions[-1][0]=='edge' else None
    def push(i,l,removed,vias,cost,actions,visited):
        family=layer_family(visited,vias);key=(i,l,removed,vias,family if diverse else None)
        signature=tuple((q[0],q[1]) for q in visited)
        token=(removed,vias,signature);side=side_of(actions) if (i,l) in goals else None
        position=(i,l,family,cuts(removed),side) if diverse and (i,l) in goals else (i,l,family) if diverse else (i,l,removed,side) if (i,l) in goals else (i,l)
        if turn_admission is not None:
            incoming=arrival(actions);key=(*key,incoming);position=(*position,incoming)
        result_key=(family,cuts(removed),side) if diverse else (removed,side)
        if (i,l) in goals and (mask_results.get(result_key,0)>=2 or not diverse and side_results.get(side,0)>=side_limit):return
        frontier=frontiers.setdefault(position,{})
        if token in frontier and frontier[token]<=cost:return
        if len(frontier)>=8 and cost>=max(frontier.values()):return
        rows=labels.setdefault(key,[])
        if any(s==signature and c<=cost for c,s in rows) or len(rows)>=3 and cost>=rows[-1][0]:return
        rows.append((cost,signature));rows.sort();del rows[3:]
        frontier[token]=cost
        if len(frontier)>8:
            worst=max(frontier,key=lambda q:(frontier[q],len(q[1]),len(q[0]),q[2]))
            del frontier[worst]
        queue=queues.setdefault(family if diverse else None,[])
        heapq.heappush(queue,(cost+heuristic(i),next(serial),i,l,removed,vias,cost,actions,visited))
    for i,l in starts:push(i,l,frozenset(),(),0,[],((i,l),))
    while any(queues.values()) and expanded<max_states and time.monotonic()<deadline:
        active=sorted(key for key,queue in queues.items() if queue) if diverse else [None]
        current=next((key for key in active if last_family is None or key>last_family),active[0]) if diverse else None
        last_family=current
        _,_,i,l,removed,vias,cost,actions,visited=heapq.heappop(queues[current])
        family=layer_family(visited,vias);signature=tuple((q[0],q[1]) for q in visited)
        key=(i,l,removed,vias,family if diverse else None)
        side=side_of(actions) if (i,l) in goals else None
        position=(i,l,family,cuts(removed),side) if diverse and (i,l) in goals else (i,l,family) if diverse else (i,l,removed,side) if (i,l) in goals else (i,l)
        if turn_admission is not None:
            incoming=arrival(actions);key=(*key,incoming);position=(*position,incoming)
        if (cost,signature) not in labels.get(key,()):continue
        if frontiers[position].get((removed,vias,signature))!=cost:continue
        expanded+=1
        word=layer_word(family);expanded_families[word]=expanded_families.get(word,0)+1
        if (i,l) in goals:
            legs=[(a[1],a[2]) for a in actions if a[0]=='edge'];via_points=[a[2] for a in actions if a[0]=='via']
            sig=physical_signature(legs,via_points)
            result_key=(family,cuts(removed),side) if diverse else (removed,side)
            if sig not in signatures and mask_results.get(result_key,0)<2 and (diverse or side_results.get(side,0)<side_limit):
                proposal={'removed':removed,'legs':legs,'vias':via_points,'actions':actions,'cost':cost,
                          'expanded':expanded,'side':side,'layer_word':word,'cut_components':cuts(removed)}
                if plan_admission is not None and not plan_admission(proposal):continue
                mask_results[result_key]=mask_results.get(result_key,0)+1
                signatures.add(sig)
                if diverse:
                    bucket=candidates.setdefault(family,{})
                    if cuts(removed) in bucket or len(bucket)<max_results:
                        bucket.setdefault(cuts(removed),[]).append(proposal)
                    if possible_families<=candidates.keys():break
                else:
                    side_results[side]=side_results.get(side,0)+1;results.append(proposal)
                    if len(results)>=max_results:break
            continue
        for j in neighbors[i]:
            if time.monotonic()>=deadline:break
            if (j,l) in visited:continue
            key=(i,j,l)
            if key not in edge_cache:edge_cache[key]=edge(points[i],points[j],l)
            step=edge_cache[key]
            if step is None:continue
            if turn_admission is not None:
                outgoing=path_heading(step['path'])
                if outgoing is None or incoming is not None and not turn_admission(incoming,outgoing,l):continue
            hits=frozenset(step['blockers']);union=removed|hits
            if not hits<=soft or len(union)>max_removed:continue
            push(j,l,union,vias,cost+step['cost']+displacement_cost*len(hits-removed),
                 actions+[('edge',l,step['path'])],visited+((j,l),))
        other=2 if l==0 else 0
        if len(vias)>=max_vias or i in vias or (i,other) in visited:continue
        if i not in via_cache:via_cache[i]=via(points[i])
        hits=via_cache[i]
        if hits is None:continue
        hits=frozenset(hits);union=removed|hits
        if not hits<=soft or len(union)>max_removed:continue
        if via_pair is not None and any(not via_pair(points[i],points[j]) for j in vias):continue
        push(i,other,union,tuple(sorted((*vias,i))),cost+via_cost+displacement_cost*len(hits-removed),
             actions+[('via',other,points[i])],visited+((i,other),))
    if diverse:
        # Build a stable layer/cut buffer before prioritizing distinct cuts.
        # Keep geometric variants available after those first choices.
        ordered=[]
        for family in sorted(candidates,key=lambda q:(q[1],q[0])):
            buckets=sorted(candidates[family].values(),key=lambda rows:min(q['cost'] for q in rows))
            ordered.append([sorted(rows,key=lambda q:q['cost']) for rows in buckets])
        for variant in range(6):
            for cut_index in range(max((len(rows) for rows in ordered),default=0)):
                for rows in ordered:
                    if cut_index<len(rows) and variant<len(rows[cut_index]):results.append(rows[cut_index][variant])
        results=select_diverse_plans(results,max_results)
        for proposal in results:side_results[proposal['side']]=side_results.get(proposal['side'],0)+1
    if diagnostic is not None:
        found_families=set(candidates) if diverse else {(q['layer_word'][0],len(q['vias'])) for q in results}
        diagnostic.update(expanded=expanded,states=len(labels),proposal_sides=dict(side_results),
                          expanded_layer_words={'>'.join(map(str,key)):value for key,value in expanded_families.items()},
                          proposal_families=[{'layer_word':q['layer_word'],'cut_components':q['cut_components']} for q in results],
                          possible_layer_words=[layer_word(q) for q in sorted(possible_families,key=lambda q:(q[1],q[0]))],
                          found_layer_words=[layer_word(q) for q in sorted(found_families,key=lambda q:(q[1],q[0]))],
                          missing_layer_words=[layer_word(q) for q in sorted(possible_families-found_families,key=lambda q:(q[1],q[0]))],
                          active_states=sum(map(len,frontiers.values())),edge_queries=len(edge_cache),via_queries=len(via_cache),
                          reason='deadline' if time.monotonic()>=deadline else 'state_cap' if expanded>=max_states else 'family_coverage' if diverse and possible_families<=candidates.keys() else 'result_cap' if not diverse and len(results)>=max_results else 'frontier_exhausted')
    return results


def select_diverse_plans(proposals,limit):
    """Try new conductor cuts before layer variants of an already chosen cut."""
    selected=[];deferred=[];cuts=set();words=set()
    for proposal in proposals:
        key=(tuple(proposal['cut_components']),proposal['side'])
        if key not in cuts:
            selected.append(proposal);cuts.add(key);words.add(tuple(proposal['layer_word']))
            if len(selected)>=limit:return selected
        else:deferred.append(proposal)
    variants=[]
    for proposal in deferred:
        word=tuple(proposal['layer_word'])
        if word not in words:
            selected.append(proposal);words.add(word)
            if len(selected)>=limit:return selected
        else:variants.append(proposal)
    return (selected+variants)[:limit]


def movable_ids(state, contract, generated_ids, *, protected, target_net):
    """Explicit field access ownership or authoritative generated provenance."""
    roles=contract['roles'];owned=set(generated_ids)|{u for u,r in roles.items() if r.endswith('_access')}
    hard=set(protected)|set(contract.get('prerequisite_tracks',{}))|{u for u,r in roles.items() if r.endswith('_trunk') or r=='row_link'}
    return {u for u in owned-hard if u in state['tracks'] and not state['tracks'][u]['locked']
            and state['tracks'][u]['net']!=target_net}


def expansion_masks(removed, contacts, movable, *, limit=12):
    """One actual-contact hop per retry, never implicit whole-chain removal."""
    removed=frozenset(removed);adjacent=set().union(set(),*(set(contacts.get(u,())) for u in removed))
    return [removed]+[removed|{u} for u in sorted(adjacent&set(movable)-removed)[:limit-1]]


def tail_attachment(tail, buses):
    """Propose a short endpoint correction onto an intact same-net bus.

    Native attachment/connectivity/creation proof is required before applying.
    The bus is never split. A projection beyond its finite segment is rejected.
    """
    if tail['class']!='PCB_TRACK' or tail['locked']:return None
    choices=[]
    for bus in buses:
        if bus['class']!='PCB_TRACK' or bus['net']!=tail['net'] or bus['layer']!=tail['layer']:continue
        a,z=bus['start'],bus['end'];d=(z[0]-a[0],z[1]-a[1]);length=d[0]**2+d[1]**2
        if not length:continue
        for name,other in (('start','end'),('end','start')):
            q=tail[name];t=((q[0]-a[0])*d[0]+(q[1]-a[1])*d[1])/length
            if not 0<t<1:continue
            point=(a[0]+t*d[0],a[1]+t*d[1]);gap=math.dist(q,point)
            if not 0<gap<=(tail['width']+bus['width'])/2 or point==tuple(tail[other]):continue
            if math.dist(tail[other],point)>=math.dist(tail[other],q):continue
            if all(type(v) is int for v in (*a,*z,*q)):point=tuple(round(v) for v in point)
            choices.append((gap,name,point))
    if not choices:return None
    _,name,point=min(choices)
    return name,point


def trial_schedule(plans, expand, *, max_trials=32, feedback=None):
    """Round-robin target geometries before deeper removal/order retries."""
    from collections import deque
    queues=[deque([(frozenset(plan['removed']),False)]) for plan in plans]
    seen=[{(frozenset(plan['removed']),False)} for plan in plans];count=0;feedback_used=set()
    while any(queues) and count<max_trials:
        for index,plan in enumerate(plans):
            if not queues[index]:continue
            removed,reverse=queues[index].popleft();count+=1
            yield plan,removed,reverse
            if count>=max_trials:return
            if feedback is not None and index not in feedback_used and not reverse:
                proposed=feedback(plan,removed)
                grown=frozenset(proposed) if proposed is not None else removed
                if removed<grown and len(grown)<=16 and (grown,False) not in seen[index]:
                    feedback_used.add(index);seen[index].add((grown,False));count+=1
                    yield plan,grown,False
                    if count>=max_trials:return
            if not reverse:
                alternatives=[(frozenset(q),False) for q in expand(plan,removed) if set(q)!=set(removed)]
                # One actual-contact expansion, then opposite constrained-net
                # order; all target geometries receive the same opportunity.
                alternatives[1:1]=[(removed,True)]
                for value in alternatives:
                    if value not in seen[index] and len(value[0])<=16:
                        seen[index].add(value);queues[index].append(value)


def bounded_region(points, *, padding, max_span):
    """Bound all actual cut/source contacts and reject oversized repairs."""
    points=[tuple(q) for q in points]
    if (not points or type(padding) is not int or padding<0 or type(max_span) is not int or max_span<=0
            or any(len(q)!=2 or any(type(v) is not int for v in q) for q in points)):
        raise ValueError('Integer bounded repair contacts required')
    bounds=(min(q[0] for q in points)-padding,min(q[1] for q in points)-padding,
            max(q[0] for q in points)+padding,max(q[1] for q in points)+padding)
    if max(bounds[2]-bounds[0],bounds[3]-bounds[1])>max_span:
        raise ValueError('Complete cut/source region exceeds finite repair extent')
    return bounds


def physical_signature(legs,vias):
    """Canonical copper union, independent of visibility vertex splitting."""
    lines={}
    for face,path in legs:
        for a,b in zip(path,path[1:]):
            if a==b:continue
            if any(type(v) is not int for v in (*a,*b)):raise ValueError('Integer copper geometry required')
            dx,dy=b[0]-a[0],b[1]-a[1];g=math.gcd(abs(dx),abs(dy));ux,uy=dx//g,dy//g
            if ux<0 or ux==0 and uy<0:ux,uy=-ux,-uy
            key=(face,ux,uy,ux*a[1]-uy*a[0])
            interval=sorted((ux*a[0]+uy*a[1],ux*b[0]+uy*b[1]))
            lines.setdefault(key,[]).append(tuple(interval))
    rows=[]
    for key,intervals in sorted(lines.items()):
        merged=[]
        for left,right in sorted(intervals):
            if merged and left<=merged[-1][1]:merged[-1]=(merged[-1][0],max(right,merged[-1][1]))
            else:merged.append((left,right))
        rows.append((key,tuple(merged)))
    return tuple(rows),tuple(sorted(set(map(tuple,vias))))
