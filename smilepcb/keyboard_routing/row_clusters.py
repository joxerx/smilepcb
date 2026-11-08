"""Physical straight row spines joined by explicitly reported octile links."""
import math
import statistics
import time


def physical_clusters(pads):
    pads=sorted(pads,key=lambda d:(d.GetPosition().x,d.GetPosition().y))
    if not pads:return []
    gaps=[b.GetPosition().x-a.GetPosition().x for a,b in zip(pads,pads[1:]) if b.GetPosition().x>a.GetPosition().x]
    if not gaps:return [pads]
    pitch=statistics.median(gaps);groups=[[pads[0]]]
    for a,b in zip(pads,pads[1:]):
        if b.GetPosition().x-a.GetPosition().x>1.5*pitch or abs(b.GetPosition().y-a.GetPosition().y)>pitch/4:
            groups.append([])
        groups[-1].append(b)
    return groups


def _link_metrics(shapes):
    vertices={};length=0;vias=0
    for shape in shapes.values():
        if shape['class']=='PCB_VIA':vias+=1;continue
        a,z=tuple(shape['start']),tuple(shape['end']);length+=math.dist(a,z)
        for point,other in ((a,z),(z,a)):
            vertices.setdefault((shape['layer'],point),[]).append((other[0]-point[0],other[1]-point[1]))
    turns={}
    for vectors in vertices.values():
        if len(vectors)!=2:continue
        a,b=vectors;denom=math.hypot(*a)*math.hypot(*b)
        if not denom:continue
        angle=round(math.degrees(math.acos(max(-1,min(1,-(a[0]*b[0]+a[1]*b[1])/denom)))))
        if angle:turns[str(angle)]=turns.get(str(angle),0)+1
    return {'length_nm':round(length),'via_count':vias,'bend_counts_degrees':turns}


def route_row_clusters(board,net,pads,*,row_layer,domain,deadline):
    """Plan once within the caller's row transaction and absolute deadline.

    Reports describe construction geometry. Final field receipts independently
    capture current live ownership after any permitted pre-receipt hygiene.
    """
    groups=physical_clusters(pads)
    if len(groups)<2 or time.monotonic()>=deadline:return None
    from smilepcb.native_worker import p,snapshot
    from .field import _seed_straight_row
    from .local_router import xy,vec
    from .matrix import KeyboardRouter
    from .native_graph import copper_graph,Endpoint
    before=snapshot(board);trunks=set();access=set();links=set();contacts=[]
    result={'net':net,'complete':False,'topology':'clustered_spines','geometry_stage':'construction',
        'pad_count':len(pads),'spines':[],'links':[],'domain_nm':list(domain)}
    def graph_now():return copper_graph(board,{net},deadline,include_zones=False)
    def roots(graph,selected):
        ids={q['item'].m_Uuid.AsString():i for i,q in enumerate(graph['nodes'])}
        if not set(selected)<=ids.keys():raise ValueError('Cluster proof omitted an actual row pad')
        return {graph['find'](ids[u]) for u in selected},ids
    for index,group in enumerate(groups):
        if time.monotonic()>=deadline:return dict(result,reason='Cluster spine budget exhausted')
        pad_ids=[d.m_Uuid.AsString() for d in group]
        if len(group)==1:
            row={'complete':True,'singleton':True,'object_ids':[],'trunk_ids':[],'access_ids':[],'pad_count':1}
        else:row=_seed_straight_row(board,net,group,row_layer=row_layer,domain=domain,deadline=deadline)
        result['spines'].append(dict(row,index=index,pad_ids=pad_ids))
        if not row['complete']:return dict(result,reason='Cluster straight spine or pad access failed')
        trunks.update(row['trunk_ids']);access.update(set(row['object_ids'])-set(row['trunk_ids']))
        graph=graph_now();components,ids=roots(graph,pad_ids)
        if len(components)!=1:raise ValueError('Cluster spine did not connect its native pads')
        candidates=[]
        for uid in row['trunk_ids']:
            node=graph['nodes'][ids[uid]];item=node['item']
            if graph['find'](ids[uid]) not in components:raise ValueError('Detached cluster spine')
            if item.GetClass()=='PCB_TRACK':
                candidates.extend((xy(q),item.GetLayer(),uid) for q in (item.GetStart(),item.GetEnd()))
        if not candidates:
            if len(group)!=1 or not group[0].IsOnLayer(row_layer):return dict(result,reason='No physical cluster contact on the requested face')
            candidates=[(xy(group[0].GetPosition()),row_layer,pad_ids[0])]
        for point,face,uid in candidates:
            node=graph['nodes'][ids[uid]]
            if face not in node['shapes'] or not p.SHAPE.Collide(p.SHAPE_CIRCLE(vec(point),0),node['shapes'][face],0):
                raise ValueError('Cluster link endpoint has no native contact witness')
        contacts.append(candidates)
    for index,(left,right) in enumerate(zip(contacts,contacts[1:])):
        if time.monotonic()>=deadline:return dict(result,reason='Cluster link budget exhausted')
        joined=[d.m_Uuid.AsString() for group in groups[:index+2] for d in group]
        if len(roots(graph_now(),joined)[0])==1:
            result['links'].append({'from_spine':index,'to_spine':index+1,'complete':True,
                'already_connected':True,'object_ids':[],'length_nm':0,'via_count':0,'bend_counts_degrees':{}})
            continue
        a,b=min(((a,b) for a in left for b in right),key=lambda pair:(math.dist(pair[0][0],pair[1][0]),pair))
        mark=snapshot(board);router=KeyboardRouter(board,deadline=min(deadline,time.monotonic()+3),compact=True,
            track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000)
        router.grid_step=250000;router.connection_budget_s=2;router.heuristic_weight=3
        router.refresh_interfaces(graph_now())
        found=router.connect(Endpoint(a[0],a[1],net),Endpoint(b[0],b[1],net))
        current=snapshot(board);added=set(current['tracks'])-set(mark['tracks'])
        if any(current['tracks'].get(u)!=v for u,v in mark['tracks'].items()):raise ValueError('Row link changed retained copper')
        shapes={u:current['tracks'][u] for u in added}
        result['links'].append({'from_spine':index,'to_spine':index+1,'left_contact':a,'right_contact':b,
            'complete':found,'object_ids':sorted(added),**_link_metrics(shapes)})
        links.update(added)
        if not found:return dict(result,reason='Cluster link budget exhausted' if time.monotonic()>=deadline else 'Bounded native cluster link search failed')
        if len(roots(graph_now(),joined)[0])!=1:raise ValueError('Row link did not join its actual field components')
    after=snapshot(board);added=set(after['tracks'])-set(before['tracks'])
    if trunks&access or trunks&links or access&links or added!=trunks|access|links:raise ValueError('Cluster field ownership is not exact and disjoint')
    if after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=v for u,v in before['tracks'].items()):
        raise ValueError('Cluster row changed its retained entry')
    for uid in added:
        shape=after['tracks'][uid]
        if shape['class']!='PCB_TRACK':continue
        dx,dy=shape['end'][0]-shape['start'][0],shape['end'][1]-shape['start'][1]
        if uid in trunks and dy or dx and dy and abs(dx)!=abs(dy):raise ValueError('Cluster row has incompatible spine or link geometry')
    if len(roots(graph_now(),[d.m_Uuid.AsString() for d in pads])[0])!=1:raise ValueError('Cluster row is not one physical component')
    if time.monotonic()>=deadline:return dict(result,reason='Cluster row proof budget exhausted')
    return dict(result,complete=True,trunk_ids=sorted(trunks),link_ids=sorted(links),access_ids=sorted(access),object_ids=sorted(added),
        trunk_paths=[path for row in result['spines'] for path in row.get('trunk_paths',[])],
        inline_vias=sum(row.get('inline_vias',0) for row in result['spines']),routed_branches=sum(row.get('routed_branches',0) for row in result['spines']))
