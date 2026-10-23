"""Prune generated free ends, retaining contacts on track interiors."""
import time
from smilepcb.native_worker import p
from smilepcb.routing_style import filled_zones


def remove_tails(board, *, protected, budget_s=30):
    stop=time.monotonic()+budget_s
    tracks={t.m_Uuid.AsString():t for t in board.GetTracks()}
    if not set(protected)<=tracks.keys():raise ValueError('Protected routing object is missing')
    cell=p.FromMM(5);buckets={p.F_Cu:{},p.B_Cu:{}};entries={}
    def xy(q):return q.x,q.y
    def index(uid,shape,layer,net,points):
        entry={'id':uid,'shape':shape,'net':net,'points':points};entries[uid,layer]=entry
        bb=shape.BBox()
        for x in range(bb.GetLeft()//cell,bb.GetRight()//cell+1):
            for y in range(bb.GetTop()//cell,bb.GetBottom()//cell+1):
                buckets[layer].setdefault((x,y),[]).append(entry)
    for item in [*tracks.values(),*(d for f in board.GetFootprints() for d in f.Pads())]:
        points=[xy(item.GetStart()),xy(item.GetEnd())] if item.GetClass()=='PCB_TRACK' else [xy(item.GetPosition())]
        for layer in buckets:
            if item.IsOnLayer(layer):index(item.m_Uuid.AsString(),item.GetEffectiveShape(layer),layer,item.GetNetname(),points)
    for i,(shape,net,layer,_) in enumerate(filled_zones(board)):
        points=[xy(shape.COutline(j).CPoint(k)) for j in range(shape.OutlineCount()) for k in range(shape.COutline(j).PointCount())]
        index('zone-'+str(i),shape,layer,net,points)
    removed=set();trimmed=set()
    def nearby(t):
        box=t.GetEffectiveShape(t.GetLayer()).BBox();seen=set()
        for x in range(box.GetLeft()//cell,box.GetRight()//cell+1):
            for y in range(box.GetTop()//cell,box.GetBottom()//cell+1):
                for other in buckets[t.GetLayer()].get((x,y),[]):
                    uid=other['id']
                    if uid in seen or uid in removed or uid==t.m_Uuid.AsString() or other['net']!=t.GetNetname():continue
                    seen.add(uid);yield other
    def touches(t,point,others):
        probe=p.SHAPE_CIRCLE(p.VECTOR2I(*point),t.GetWidth()//2)
        return any(p.SHAPE.Collide(probe,e['shape'],0) for e in others)
    def first_contact(t,a,b,others):
        dx,dy=b[0]-a[0],b[1]-a[1];length=dx*dx+dy*dy
        if not length:return None
        candidates={b}
        for e in others:
            for q in e['points']:
                fraction=((q[0]-a[0])*dx+(q[1]-a[1])*dy)/length
                if 0<fraction<=1:candidates.add((round(a[0]+fraction*dx),round(a[1]+fraction*dy)))
            if len(e['points'])==2:
                c,d=e['points'];ex,ey=d[0]-c[0],d[1]-c[1];den=dx*ey-dy*ex
                if den:
                    f=((c[0]-a[0])*ey-(c[1]-a[1])*ex)/den
                    g=((c[0]-a[0])*dy-(c[1]-a[1])*dx)/den
                    if 0<f<=1 and 0<=g<=1:candidates.add((round(a[0]+f*dx),round(a[1]+f*dy)))
        valid=[q for q in candidates if touches(t,q,others)]
        return min(valid,key=lambda q:(q[0]-a[0])**2+(q[1]-a[1])**2) if valid else None
    def redundant_via(via):
        # A single-layer via can still be a copper junction between disjoint
        # contacts. Delete it only if all landing copper is locally connected
        # without its annulus. Filled polygons count as real layer contacts.
        uid=via.m_Uuid.AsString();live=[]
        for layer in buckets:
            contacts={};shape=via.GetEffectiveShape(layer);box=shape.BBox()
            for x in range(box.GetLeft()//cell,box.GetRight()//cell+1):
                for y in range(box.GetTop()//cell,box.GetBottom()//cell+1):
                    for other in buckets[layer].get((x,y),()):
                        key=other['id']
                        if key==uid or key in removed or key in contacts or other['net']!=via.GetNetname():continue
                        if p.SHAPE.Collide(shape,other['shape'],0):contacts[key]=other['shape']
            if contacts:live.append(list(contacts.values()))
        if len(live)>1:return False
        if not live:return True
        contacts=live[0];reached={0};changed=True
        while changed:
            if time.monotonic()>stop:raise TimeoutError('Generated via cleanup exceeded its budget')
            changed=False
            for i,shape in enumerate(contacts):
                if i in reached:continue
                if any(p.SHAPE.Collide(shape,contacts[j],0) for j in reached):reached.add(i);changed=True
        return len(reached)==len(contacts)
    changed=True
    while changed:
        changed=False
        for uid,t in tracks.items():
            if time.monotonic()>stop:raise TimeoutError('Generated tail cleanup exceeded its budget')
            if uid in removed or uid in protected or t.IsLocked() or t.GetClass()!='PCB_TRACK':continue
            others=list(nearby(t))
            for end in ('start','end'):
                a=xy(t.GetStart() if end=='start' else t.GetEnd());b=xy(t.GetEnd() if end=='start' else t.GetStart())
                if touches(t,a,others):continue
                contact=first_contact(t,a,b,others)
                if contact is None or contact==b:
                    removed.add(uid);board.Delete(t);changed=True;break
                if end=='start':t.SetStart(p.VECTOR2I(*contact))
                else:t.SetEnd(p.VECTOR2I(*contact))
                entry=entries[uid,t.GetLayer()];entry['shape']=t.GetEffectiveShape(t.GetLayer())
                entry['points']=[xy(t.GetStart()),xy(t.GetEnd())]
                trimmed.add(uid);changed=True
        for uid,t in tracks.items():
            if time.monotonic()>stop:raise TimeoutError('Generated via cleanup exceeded its budget')
            if uid in removed or uid in protected or t.IsLocked() or not isinstance(t,p.PCB_VIA):continue
            if redundant_via(t):removed.add(uid);board.Delete(t);changed=True
    return {'removed_ids':sorted(removed),'trimmed_ids':sorted(trimmed-removed)}


def prune_reported_tails(board,findings,*,protected,budget_s=30):
    """Remove only native-reported tails, preserving the actual pad partition.

    Cap-only contacts can be electrically necessary even when native DRC flags
    a free centreline end. Such a removal is rolled back, never waived.
    """
    from smilepcb.native_worker import snapshot
    from .completion import pad_partitions
    stop=time.monotonic()+budget_s;before=snapshot(board)
    tracks={t.m_Uuid.AsString():t for t in board.GetTracks()};selected=set()
    for finding in findings:
        if finding.get('type')!='track_dangling' or finding.get('severity')!='warning' or len(finding.get('items',()))!=1:
            raise ValueError('Only native track-dangling warnings are eligible')
        uid=finding['items'][0].get('uuid');item=tracks.get(uid)
        if item is None or item.GetClass()!='PCB_TRACK' or item.IsLocked() or uid in protected:
            raise ValueError('Reported tail is missing, locked or protected')
        selected.add(uid)
    if not selected:raise ValueError('No reported tails to remove')
    original={frozenset(group) for group in pad_partitions(board,stop)}
    copies={uid:tracks[uid].Duplicate() for uid in selected}
    for uid,item in copies.items():item.SetUuid(p.KIID(uid))
    try:
        for uid in selected:board.Delete(tracks[uid])
        if not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Reported-tail refill failed')
        if {frozenset(group) for group in pad_partitions(board,stop)}!=original:
            raise ValueError('Reported tail is electrically necessary')
        after=snapshot(board)
        if after['footprints']!=before['footprints'] or any(after['tracks'].get(uid)!=v for uid,v in before['tracks'].items() if uid not in selected):
            raise ValueError('Reported cleanup changed retained objects')
        if time.monotonic()>=stop:raise TimeoutError('Reported-tail cleanup budget exhausted')
    except BaseException:
        for item in copies.values():board.Add(item)
        if not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Cannot restore rejected tail cleanup')
        raise
    return {'removed_ids':sorted(selected),'pad_partition_preserved':True}


def normalize_shallow_branches(board, *, editable_ids, budget_s=30):
    """Replace acute generated endpoint joins with native-clear 45-degree legs.

    Retain every existing copper contact and omit already covered bus pieces.
    Callers own refill and full native acceptance; this never saves a project.
    """
    import math
    from collections import defaultdict
    from smilepcb.native_worker import snapshot
    from smilepcb.routing_style import octile_paths
    from smilepcb.routing_copper import uncovered
    from .local_router import LocalRouter,xy,vec
    if type(budget_s) not in (int,float) or not math.isfinite(budget_s) or not 0<budget_s<=45:
        raise ValueError('Junction cleanup budget must be positive and at most45 seconds')
    stop=time.monotonic()+budget_s;before=snapshot(board);coverage=dict(before['tracks']);editable=set(editable_ids)
    if editable-before['tracks'].keys():raise ValueError('Unknown editable routing object')
    tracks={t.m_Uuid.AsString():t for t in board.GetTracks()};ends=defaultdict(list)
    for uid,t in tracks.items():
        if t.GetClass()!='PCB_TRACK':continue
        for point in (xy(t.GetStart()),xy(t.GetEnd())):ends[t.GetNetname(),t.GetLayer(),point].append(uid)
    added=[];removed=[];changes=0
    for uid in sorted(editable):
        if time.monotonic()>=stop:raise TimeoutError('Junction cleanup deadline exhausted')
        t=tracks.get(uid)
        if t is None or t.IsLocked() or t.GetClass()!='PCB_TRACK':continue
        a,b=xy(t.GetStart()),xy(t.GetEnd());dx,dy=b[0]-a[0],b[1]-a[1]
        if not dx or not dy or abs(dx)==abs(dy):continue
        net=t.GetNetname();layer=t.GetLayer();width=t.GetWidth();shallow=False
        for joint,far in ((a,b),(b,a)):
            v=(far[0]-joint[0],far[1]-joint[1])
            for other_id in ends[net,layer,joint]:
                other=tracks.get(other_id)
                if other is None or other_id==uid:continue
                q=xy(other.GetEnd()) if xy(other.GetStart())==joint else xy(other.GetStart())
                w=(q[0]-joint[0],q[1]-joint[1]);length=math.hypot(*v)*math.hypot(*w)
                if length and math.sqrt(.5)<(v[0]*w[0]+v[1]*w[1])/length<1:shallow=True
        if not shallow:continue
        oracle=LocalRouter(board,deadline=stop,ignored_copper={uid})
        old=t.GetEffectiveShape(layer);contacts={}
        for cell in oracle.cells(old.BBox()):
            for key,shape,other_net,_ in oracle.buckets[layer].get(cell,()):
                if key!=uid and other_net==net and key not in contacts and p.SHAPE.Collide(old,shape,0):contacts[key]=shape
        existing=[value for key,value in coverage.items() if key!=uid]
        choices=[]
        for path in octile_paths(a,b):
            shapes=[p.SHAPE_SEGMENT(vec(x),vec(z),width) for x,z in zip(path,path[1:])]
            if not all(oracle.clear(shape,layer,net) for shape in shapes):continue
            if any(not any(p.SHAPE.Collide(shape,contact,0) for shape in shapes) for contact in contacts.values()):continue
            pieces=[]
            for x,z in zip(path,path[1:]):
                item={'class':'PCB_TRACK','start':list(x),'end':list(z),'width':width,'layer':layer,'net':net,'locked':False}
                pieces.extend(uncovered(item,existing))
            choices.append((sum(math.dist(v['start'],v['end']) for v in pieces),pieces))
        if not choices:continue
        _,pieces=min(choices,key=lambda v:v[0])
        if time.monotonic()>=stop:raise TimeoutError('Junction cleanup deadline exhausted')
        board.Delete(t);tracks.pop(uid);coverage.pop(uid);removed.append(uid)
        for item in pieces:
            segment=p.PCB_TRACK(board);segment.SetStart(vec(item['start']));segment.SetEnd(vec(item['end']))
            segment.SetLayer(layer);segment.SetWidth(width);segment.SetNet(board.FindNet(net));board.Add(segment)
            new_id=segment.m_Uuid.AsString();added.append(new_id);coverage[new_id]=item
        changes+=1
    return {'changes':changes,'removed_ids':removed,'added_ids':added}


def cleanup_generated(board, *, protected):
    """Clean only editable copper and preserve the exact native pad partition.

    Existing open groups are permitted here; no connected or disconnected pad
    group may change as a side effect. Saving remains the worker's last step.
    """
    from collections import defaultdict
    from smilepcb.native_worker import snapshot
    from .native_graph import copper_graph
    stop=time.monotonic()+60;before=snapshot(board);protected=set(protected)
    if protected-before['tracks'].keys():raise ValueError('Protected routing object is missing')
    def partition():
        nets={pad.GetNetname() for f in board.GetFootprints() for pad in f.Pads()
              if pad.GetNumber() and pad.GetNetname()}
        graph=copper_graph(board,nets,stop);groups=defaultdict(set)
        for i,node in enumerate(graph['nodes']):
            item=node['item']
            if isinstance(item,p.PAD) and item.GetNumber():
                groups[node['net'],graph['find'](i)].add(item.m_Uuid.AsString())
        return frozenset((net,frozenset(pads)) for (net,_),pads in groups.items())
    original=partition()
    zones=[z.Duplicate(False) for z in board.Zones() if not z.GetIsRuleArea()]
    for z in list(board.Zones()):
        if not z.GetIsRuleArea():board.Delete(z)
    editable=before['tracks'].keys()-protected
    junctions=normalize_shallow_branches(board,editable_ids=editable,budget_s=min(30,stop-time.monotonic()))
    for z in zones:board.Add(z)
    if zones and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Cannot refill after junction cleanup')
    result=remove_tails(board,protected=protected,budget_s=min(30,stop-time.monotonic()))
    if zones and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Cannot refill after tail cleanup')
    after=snapshot(board)
    if after['footprints']!=before['footprints']:raise ValueError('Cleanup changed a footprint')
    immutable=protected|{uid for uid,t in before['tracks'].items() if t['locked']}
    if any(after['tracks'].get(uid)!=before['tracks'][uid] for uid in immutable):raise ValueError('Cleanup changed protected copper')
    if partition()!=original:raise ValueError('Cleanup changed native pad connectivity')
    return dict(result,junctions=junctions,pad_partition_preserved=True)
