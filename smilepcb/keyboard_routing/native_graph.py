"""Connectivity of actual filled polygons, not zone bounding outlines."""
import heapq
import itertools
import time
from smilepcb.native_worker import p
from .local_router import xy,distance,vec


class Endpoint:
    def __init__(self,position,layer,net):self.position=position;self.layer=layer;self.net=net
    def GetPosition(self):return vec(self.position)
    def GetNetname(self):return self.net
    def IsOnLayer(self,layer):return layer==self.layer


def island_points(shape):
    chains=[chain for i in range(shape.OutlineCount())
            for chain in [shape.COutline(i),*(shape.CHole(i,j) for j in range(shape.HoleCount(i)))]]
    total=sum(c.PointCount() for c in chains);stride=max(1,(total+63)//64)
    points=[];offset=0;next_sample=0
    for chain in chains:
        size=chain.PointCount()
        while next_sample<offset+size:
            points.append(xy(chain.CPoint(next_sample-offset)));next_sample+=stride
        offset+=size
    return points


def nearest_on_island(shape,position):
    point=vec(position)
    if shape.Contains(point):return position
    chains=[chain for i in range(shape.OutlineCount())
            for chain in [shape.COutline(i),*(shape.CHole(i,j) for j in range(shape.HoleCount(i)))]]
    return min((xy(c.NearestPoint(point)) for c in chains),key=lambda q:distance(position,q))


def routing_points(node):
    """Choose track-center contacts inside a filled island, not on its rim.

    A zone boundary is already the clearance boundary of foreign copper.
    Centering a finite-width track there can make the endpoint itself illegal.
    Erode an owned copy for routing anchors; retain the exact native polygon
    for connectivity and the normal collision oracle for every emitted path.
    """
    if not node['zone']:return node['points']
    if 'routing_shape' not in node:
        actual=next(iter(node['shapes'].values()))
        interior=p.SHAPE_POLY_SET(actual)
        interior.Inflate(-p.FromMM(.11),p.CORNER_STRATEGY_ROUND_ALL_CORNERS,p.FromMM(.001))
        node['routing_shape']=interior if interior.OutlineCount() else actual
        node['routing_points']=island_points(node['routing_shape'])
    return node['routing_points']


def nearest_to_node(node,position):
    if node['zone']:
        routing_points(node)
        return nearest_on_island(node['routing_shape'],position)
    item=node['item']
    if isinstance(item,p.PCB_TRACK) and not isinstance(item,p.PCB_VIA):
        return xy(p.SEG(item.GetStart(),item.GetEnd()).NearestPoint(vec(position)))
    return node['points'][0]


def copper_graph(board,nets,deadline=None,*,include_zones=True):
    phase=['load conductors']
    def budget():
        if deadline is not None and time.monotonic()>=deadline:raise TimeoutError('Native graph budget exhausted: '+phase[0])
    nodes=[]
    for item in [*board.GetTracks(),*(pad for f in board.GetFootprints() for pad in f.Pads())]:
        budget()
        if item.GetNetname() not in nets:continue
        layers={l:item.GetEffectiveShape(l) for l in (p.F_Cu,p.B_Cu) if item.IsOnLayer(l)}
        if isinstance(item,p.PCB_TRACK):points=[xy(item.GetStart()),xy(item.GetEnd())]
        else:points=[xy(item.GetPosition())]
        nodes.append(dict(item=item,shapes=layers,points=points,net=item.GetNetname(),zone=False))
    phase[0]='load filled polygons'
    for zone in board.Zones() if include_zones else ():
        budget()
        if zone.GetIsRuleArea() or zone.GetNetname() not in nets:continue
        if not zone.IsFilled() or zone.NeedRefill():
            raise ValueError('Refill copper zones before building connectivity')
        for layer in (p.F_Cu,p.B_Cu):
            if not zone.IsOnLayer(layer):continue
            # Multi-layer zones have no single GetLayer(). Check availability
            # before entering KiCad's getter, which aborts on a missing layer.
            if not zone.HasFilledPolysForLayer(layer):
                raise ValueError('Refill copper zones; missing filled layer')
            filled=zone.GetFilledPolysList(layer)
            for i in range(filled.OutlineCount()):
                budget();shape=filled.UnitSet(i);points=island_points(shape)
                nodes.append(dict(item=zone,shapes={layer:shape},points=points,net=zone.GetNetname(),zone=True))
    parents=list(range(len(nodes)))
    def find(i):
        while parents[i]!=i:parents[i]=parents[parents[i]];i=parents[i]
        return i
    def join(i,j):parents[find(i)]=find(j)
    buckets={p.F_Cu:{},p.B_Cu:{}};cell=p.FromMM(5)
    for i,n in enumerate(nodes):
        phase[0]=f'contact graph {i}/{len(nodes)}'
        budget()
        for layer,shape in n['shapes'].items():
            box=shape.BBox();seen=set()
            for x in range(box.GetLeft()//cell,box.GetRight()//cell+1):
                for y in range(box.GetTop()//cell,box.GetBottom()//cell+1):
                    budget()
                    for j in buckets[layer].get((x,y),[]):
                        if j in seen or nodes[j]['net']!=n['net'] or find(i)==find(j):continue
                        seen.add(j)
                        # Distinct UnitSet polygons from one native filled
                        # zone are separate islands by construction. Testing
                        # their intertwined boundaries against each other is
                        # both redundant and disproportionately expensive.
                        if n['zone'] and nodes[j]['zone'] and n['item'].m_Uuid==nodes[j]['item'].m_Uuid:
                            continue
                        if p.SHAPE.Collide(shape,nodes[j]['shapes'][layer],0):join(i,j)
                    buckets[layer].setdefault((x,y),[]).append(i)
    groups={}
    for i in range(len(nodes)):groups.setdefault(find(i),[]).append(i)
    return dict(nodes=nodes,groups=groups,find=find,join=join)


def stitch_zones(board,router,nets,include_signals=False):
    try:graph=copper_graph(board,nets,router.deadline)
    except TimeoutError as error:
        router.graph_diagnostic={'budget_exhausted':str(error)};return 0
    router.graph_diagnostic={'nodes':len(graph['nodes']),'components_before':len(graph['groups'])}
    nodes=graph['nodes'];find=graph['find'];join=graph['join'];count=0
    # First stitch overlapping real filled islands across layers. The via must
    # touch both actual polygons and clear all native copper, holes and edges.
    for i,a in enumerate(nodes):
        if router.expired():break
        if not a['zone']:continue
        for j in range(i):
            if router.expired():break
            b=nodes[j]
            if not b['zone'] or a['net']!=b['net'] or find(i)==find(j):continue
            la=next(iter(a['shapes']));lb=next(iter(b['shapes']))
            if la==lb:continue
            sa,sb=a['shapes'][la],b['shapes'][lb];ba,bb=sa.BBox(),sb.BBox()
            x0,x1=max(ba.GetLeft(),bb.GetLeft()),min(ba.GetRight(),bb.GetRight())
            y0,y1=max(ba.GetTop(),bb.GetTop()),min(ba.GetBottom(),bb.GetBottom())
            if x0>=x1 or y0>=y1:continue
            center=((x0+x1)//2,(y0+y1)//2);choices=[center,*a['points'],*b['points']]
            for radius in range(500000,5000001,500000):
                choices.extend((center[0]+dx*radius,center[1]+dy*radius) for dx,dy in
                               ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)))
            for pos in sorted(set(choices),key=lambda q:distance(center,q))[:512]:
                if router.expired():break
                if sa.Contains(vec(pos)) and sb.Contains(vec(pos)) and router.via_clear(pos,a['net']):
                    router.add_via(pos,a['net']);join(i,j);count+=1;break
    # Then bridge remaining components from sampled native copper boundary
    # points. Candidates are geometric, and the normal route collision oracle
    # still decides whether any track/via can be emitted.
    by_pair={};serial=itertools.count()
    for i,a in enumerate(nodes):
        if router.expired():break
        for j in range(i):
            if router.expired():break
            b=nodes[j]
            if a['net']!=b['net'] or find(i)==find(j) or not(include_signals or a['zone'] or b['zone']):continue
            choices=[]
            for pa in routing_points(a):
                pb=nearest_to_node(b,pa);choices.append((distance(pa,pb),pa,pb))
            for pb in routing_points(b):
                pa=nearest_to_node(a,pb);choices.append((distance(pa,pb),pa,pb))
            points=heapq.nsmallest(2,choices)
            key=tuple(sorted((find(i),find(j))));group=by_pair.setdefault(key,[])
            for length,pa,pb in points:
                candidate=(-length,next(serial),i,j,pa,pb)
                if len(group)<64:heapq.heappush(group,candidate)
                elif length < -group[0][0]:heapq.heapreplace(group,candidate)
    # A ratsnest's closest two displayed objects are not the only legal
    # terminals. Try the actual conductor components, including track ends.
    outer_deadline=router.deadline;attempts=[]
    groups=[sorted((-length,i,j,pa,pb) for length,_,i,j,pa,pb in group) for group in by_pair.values()]
    ordered=[candidate for row in itertools.zip_longest(*groups) for candidate in row if candidate is not None]
    for _,i,j,pa,pb in ordered:
        if router.expired():break
        if find(i)==find(j):continue
        a,b=nodes[i],nodes[j]
        router.deadline=min(outer_deadline or float('inf'),time.monotonic()+2)
        routed=False
        for la in a['shapes']:
            for lb in b['shapes']:
                if router.connect(Endpoint(pa,la,a['net']),Endpoint(pb,lb,b['net'])):
                    join(i,j);count+=1;routed=True;break
            else:continue
            break
        router.deadline=outer_deadline
        attempts.append({'net':a['net'],'routed':routed,'start':pa,'end':pb})
    router.graph_diagnostic['attempts']=attempts
    return count


def attach_vias(board,router,ids):
    """Reconnect individual via layers; graph connectivity alone hides them."""
    vias=[t for t in board.GetTracks() if t.m_Uuid.AsString() in ids and isinstance(t,p.PCB_VIA)]
    if {t.m_Uuid.AsString() for t in vias}!=set(ids):raise ValueError('Expected actual reported via UUIDs')
    graph=copper_graph(board,{v.GetNetname() for v in vias},router.deadline);count=0;outer=router.deadline
    for via in vias:
        origin=xy(via.GetPosition());net=via.GetNetname()
        for layer in (p.F_Cu,p.B_Cu):
            shape=via.GetEffectiveShape(layer)
            if any(node['item'].m_Uuid!=via.m_Uuid and node['net']==net and layer in node['shapes']
                   and p.SHAPE.Collide(shape,node['shapes'][layer],0) for node in graph['nodes']):continue
            choices=[]
            for node in graph['nodes']:
                if node['item'].m_Uuid==via.m_Uuid or node['net']!=net or layer not in node['shapes']:continue
                point=nearest_to_node(node,origin)
                if distance(origin,point)>p.FromMM(.01):choices.append((distance(origin,point),point))
            for _,target in sorted(choices)[:32]:
                if router.expired():break
                router.deadline=min(outer or float('inf'),time.monotonic()+2)
                mark=len(router.added)
                ok=router.connect(Endpoint(origin,layer,net),Endpoint(target,layer,net))
                router.deadline=outer
                if ok and len(router.added)>mark:count+=1;break
    return count


def route_components(board,router,nets,*,interface_refs=(),required_pad_ids=None):
    from .grid_search import search_many
    graph=copper_graph(board,nets,router.deadline);nodes=graph['nodes'];join=graph['join'];find=graph['find']
    def terminals(indices):
        return sorted({(point,layer) for i in indices for point in routing_points(nodes[i]) for layer in nodes[i]['shapes']})
    groups=[(indices,terminals(indices)) for indices in graph['groups'].values()
            if any(isinstance(nodes[i]['item'],p.PAD) and nodes[i]['item'].GetNumber()
                   and (required_pad_ids is None or nodes[i]['item'].m_Uuid.AsString() in required_pad_ids)
                   for i in indices)]
    anchors=set();attached=set()
    if interface_refs:
        for indices,_ in groups:
            refs={nodes[i]['item'].GetParentFootprint().GetReference() for i in indices
                  if isinstance(nodes[i]['item'],p.PAD)}
            if refs & set(interface_refs):
                anchors.add(indices[0])
                if refs-set(interface_refs):attached.add(nodes[indices[0]]['net'])
    def bounded(points,toward):
        if len(points)<=64:return points
        center=(sum(p[0][0] for p in toward)/len(toward),sum(p[0][1] for p in toward)/len(toward))
        near=sorted(points,key=lambda v:distance(v[0],center))[:32]
        # Keep spatial coverage on every physically reachable layer as well
        # as nearby contacts. A long F.Cu bus must not hide its only B.Cu
        # plated terminal when new vias are blocked by real geometry.
        by_layer={}
        for contact in points:by_layer.setdefault(contact[1],[]).append(contact)
        quota=32//len(by_layer);spread=[]
        for contacts in by_layer.values():
            size=min(quota,len(contacts))
            spread.extend(contacts[i*(len(contacts)-1)//max(1,size-1)] for i in range(size))
        return sorted(set(near+spread))
    def contacts(indices,points,toward):
        # Endpoints alone can hide a legal branch into the middle of a long
        # conductor. Project the other component's anchors onto native copper;
        # keep the search bounded and let its collision oracle vet each lead.
        closest=[];serial=itertools.count()
        for i in indices:
            if router.expired():break
            node=nodes[i]
            for position,_ in toward:
                point=nearest_to_node(node,position)
                length=distance(point,position)
                for layer in node['shapes']:
                    entry=(-length,next(serial),(point,layer))
                    if len(closest)<32:heapq.heappush(closest,entry)
                    elif length < -closest[0][0]:heapq.heapreplace(closest,entry)
        return bounded(sorted(set(points)|{value for _,_,value in closest}),toward)
    pairs=[]
    for i,(a,pa) in enumerate(groups):
        if router.expired():break
        for b,pb in groups[:i]:
            if router.expired():break
            if nodes[a[0]]['net']!=nodes[b[0]]['net'] or not pa or not pb:continue
            if interface_refs and (nodes[a[0]]['net'] in attached or not ({a[0],b[0]} & anchors)):continue
            aa=bounded(pa,pb);bb=bounded(pb,pa)
            aa,bb=contacts(a,aa,bb),contacts(b,bb,aa)
            length=min(distance(x[0],y[0]) for x in aa for y in bb)
            pairs.append((length,a,b,aa,bb))
    count=0;outer=router.deadline;attempts=[]
    def attempt(starts,targets,net):
        stop=min(outer or float('inf'),time.monotonic()+getattr(router,'connection_budget_s',5))
        avoidance=getattr(router,'routing_penalty',None)
        fallback=avoidance and getattr(router,'allow_penalty_fallback',True)
        router.deadline=min(stop,time.monotonic()+min(2,(stop-time.monotonic())/2)) if fallback else stop
        ok=search_many(router,starts,targets,net)
        if fallback and not ok and time.monotonic()<stop:
            # A preference must not spend the entire connection budget
            # exploring detours when crossing generated copper is necessary.
            router.routing_penalty=None;router.deadline=stop
            try:ok=search_many(router,starts,targets,net)
            finally:router.routing_penalty=avoidance
        router.deadline=outer
        return ok
    # A short but inaccessible component pair must not consume the entire
    # stage through repeated alternatives of the same net. Share attempts
    # across nets, retaining nearest-first ordering within each one.
    by_net={}
    for pair in sorted(pairs):by_net.setdefault(nodes[pair[1][0]]['net'],[]).append(pair)
    attempt_counts=getattr(router,'attempt_counts',{})
    net_order=sorted(by_net,key=lambda net:(attempt_counts.get(net,0),-by_net[net][0][0] if interface_refs else 0))
    ordered=(candidate for row in itertools.zip_longest(*(by_net[net] for net in net_order))
             for candidate in row if candidate is not None)
    for _,a,b,aa,bb in ordered:
        if router.expired():break
        if find(a[0])==find(b[0]):continue
        if interface_refs and nodes[a[0]]['net'] in attached:continue
        if len(bb)>len(aa):aa,bb=bb,aa
        ok=attempt(aa,bb,nodes[a[0]]['net'])
        if not ok and not router.expired():
            ok=attempt(bb,aa,nodes[a[0]]['net'])
        attempts.append({'net':nodes[a[0]]['net'],'connected':ok,'sources':len(aa),'targets':len(bb),
                         'search':getattr(router,'last_search',None)})
        net=nodes[a[0]]['net'];attempt_counts[net]=attempt_counts.get(net,0)+1
        if ok:
            join(a[0],b[0]);count+=1
            if interface_refs:attached.add(nodes[a[0]]['net'])
    router.graph_diagnostic={'attempts':attempts,'attempt_counts':dict(attempt_counts)};return count


def negotiate_routes(board,nets,protected,deadline,*,router_factory=None,attempt_counts=None,interface_refs=(),required_pad_ids=None):
    """Route a constrained component first, removing only generated blockers.

    All pads, edges and protected copper remain hard obstacles. Candidate
    tracks are checked against the complete pre-route oracle before any old
    object is removed. The next native DRC identifies displaced connections.
    """
    from .local_router import LocalRouter
    factory=router_factory or LocalRouter
    before={t.m_Uuid.AsString():t for t in board.GetTracks()}
    if not set(protected)<=before.keys():raise ValueError('Missing protected source copper')
    if required_pad_ids is not None:
        actual={d.m_Uuid.AsString() for f in board.GetFootprints() for d in f.Pads() if d.GetNetname() in nets}
        if len(set(required_pad_ids))<2 or not set(required_pad_ids)<=actual:
            raise ValueError('Negotiation requires actual pads on the selected nets')
    soft={uid for uid,t in before.items() if uid not in protected and not t.IsLocked() and t.GetNetname() not in nets}
    # Reserve a bounded part of the same deadline for cutting blockers; no
    # phase gets a fresh budget after the caller's deadline has elapsed.
    reserve=min(1,max(0,deadline-time.monotonic())/10)
    world=factory(board,deadline=deadline,compact=True)
    relaxed=factory(board,deadline=deadline-reserve,compact=True,ignored_copper=soft)
    relaxed.attempt_counts=attempt_counts if attempt_counts is not None else {}
    relaxed.grid_step=125000;relaxed.heuristic_weight=3
    # Disturbing existing generated copper is possible, but a clear detour
    # should be cheaper than casually cutting another completed signal.
    # This is a search preference only; hard obstacles and final validation
    # remain unchanged. Native feedback still decides whether to retain it.
    displacement_cost=getattr(relaxed,'displacement_cost_nm',p.FromMM(20))
    relaxed.routing_penalty=lambda shape,layer,net,via: displacement_cost*len(
        set(world.collisions(shape,layer,net,via)) & soft)
    count=route_components(board,relaxed,nets,interface_refs=interface_refs,required_pad_ids=required_pad_ids)
    new={t.m_Uuid.AsString():t for t in board.GetTracks() if t.m_Uuid.AsString() in relaxed.added}
    blockers=set()
    for track in new.values():
        for layer in (p.F_Cu,p.B_Cu):
            if track.IsOnLayer(layer):
                blockers.update(world.collisions(track.GetEffectiveShape(layer),layer,track.GetNetname(),isinstance(track,p.PCB_VIA)))
    if not blockers<=soft:
        for track in new.values():board.Delete(track)
        details=[]
        for uid in sorted(blockers-soft):
            item=before.get(uid)
            details.append({'id':uid,'class':item.GetClass() if item else None,'net':item.GetNetname() if item else None})
        raise ValueError('Negotiation touched a hard routing obstacle: '+repr(details))
    from .carve import retain_clear_fragments
    carve_oracle=factory(board,deadline=deadline,compact=True,ignored_copper=blockers)
    retained=[];removed_length=0
    for uid in sorted(blockers):
        original=before[uid]
        fragments=retain_clear_fragments(original,carve_oracle)
        if original.GetClass()=='PCB_TRACK':removed_length+=original.GetLength()
        board.Delete(original)
        for fragment in fragments:
            board.Add(fragment);carve_oracle.index(fragment);retained.append(fragment)
    return {'connected':count,'removed':sorted(blockers),'added':relaxed.added,
            'retained_fragments':[t.m_Uuid.AsString() for t in retained],
            'displaced_length_mm':removed_length/1e6,
            'retained_length_mm':sum(t.GetLength() for t in retained)/1e6,
            'diagnostic':relaxed.graph_diagnostic}
