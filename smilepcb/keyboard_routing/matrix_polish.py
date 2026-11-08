"""Reference-derived, fixed-terminal native matrix feeder optimization.

The incumbent is retained unless every numbered-pad partition, protected
object and physical footprint survives. Geometry scores never waive contacts.
"""
import math,time
from smilepcb.native_worker import p,snapshot
from smilepcb.routing_quality import copper_metrics
from .local_router import LocalRouter,vec,xy
from .completion import pad_partitions
from .follow_geometry import foreign_chains,offset_path,follow_spans,simplify
from .polish_geometry import chains,shortcut_candidates,shape_score,octile,valid_shape
from .parallel_neighbors import ParallelNeighbors


def _pad_entry_clear(board,path,face,net):
    ends=[p.SHAPE_CIRCLE(vec(q),0) for q in (path[0],path[-1])]
    segments=[p.SHAPE_SEGMENT(vec(a),vec(z),0) for a,z in zip(path,path[1:])]
    for f in board.GetFootprints():
        for d in f.Pads():
            if not d.GetNumber() or d.GetNetname()!=net or not d.IsOnLayer(face):continue
            shape=d.GetEffectiveShape(face)
            if any(p.SHAPE.Collide(s,shape,0) for s in segments) and not any(p.SHAPE.Collide(q,shape,0) for q in ends):return False
    return True


def _joins_clear(board,path,face,net):
    for a,z in ((path[0],path[1]),(path[-1],path[-2])):
        ray=(z[0]-a[0],z[1]-a[1])
        for t in board.GetTracks():
            if t.GetClass()!='PCB_TRACK' or t.GetNetname()!=net or t.GetLayer()!=face:continue
            u,v=xy(t.GetStart()),xy(t.GetEnd())
            if a not in (u,v):continue
            other=v if u==a else u;d=(other[0]-a[0],other[1]-a[1]);dot=sum(ray[i]*d[i] for i in (0,1))
            if dot>0:return False
    return True


def polish_matrix(board,*,protected_ids,eligible_nets,deadline,ports=None,bend_price_nm=2000000,group_strength=.9,deferred_zone_ids=()):
    started=time.monotonic();before=snapshot(board);protected=set(protected_ids);eligible=set(eligible_nets)
    if not protected<=set(before['tracks']):raise ValueError('Polish protected source is missing')
    if type(bend_price_nm) is not int or not 0<=bend_price_nm<=10000000 or not math.isfinite(group_strength) or not 0<=group_strength<=4:raise ValueError('Invalid fixed-terminal optimization cost')
    report={'accepted':False,'adopted_chains':0,'rewrites':[],'all_pad_partitions_exact':True,'source_exact':True}
    if deadline-started<3:return dict(report,reason='No optimization budget; incumbent retained',elapsed_s=time.monotonic()-started)
    copies=[]
    for t in board.GetTracks():
        copy=t.Duplicate();copy.SetUuid(p.KIID(t.m_Uuid.AsString()));copies.append(copy)
    def rollback():
        for t in list(board.GetTracks()):board.Delete(t)
        for t in copies:board.Add(t)
        board.BuildConnectivity()
        if board.Zones() and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Cannot restore pre-polish fill')
        if snapshot(board)!=before:raise ValueError('Polish rollback changed source')
    try:
        partitions={frozenset(g) for g in pad_partitions(board,deadline)}
        from .bundle_polish import extract_chains,retained_contacts_clear
        from .bundle_geometry import quality_improves,paths_clear
        work_stop=deadline-max(2,min(10,(deadline-started)/4));tick=time.monotonic()
        work=extract_chains(board,eligible_nets=eligible,protected_ids=protected,deadline=work_stop,deferred_zone_ids=deferred_zone_ids,max_points=32,min_length_nm=0)
        report['chain_extraction_s']=time.monotonic()-tick;report['contact_rejections']=0
        global_bends=copper_metrics(before['tracks'])['bends']
        work.sort(key=lambda q:(-(shape_score(q['path'],bend_price_nm)-min(shape_score(p,bend_price_nm) for p in octile(q['path'][0],q['path'][-1]))),q['net'],q['path']))
        for row in work:
            if time.monotonic()>=work_stop:break
            old=row['path'];net=row['net'];face=row['layer'];width=row['width']
            # A fixed-terminal straight line cannot improve without adding a bend.
            if len(old)<=2 or len(old)>32:continue
            items={t.m_Uuid.AsString():t for t in board.GetTracks()}
            if not set(row['ids'])<=set(items):raise ValueError('Polish chain inventory changed')
            restore=[]
            for uid in row['ids']:
                copy=items[uid].Duplicate();copy.SetUuid(p.KIID(uid));restore.append(copy)
            for uid in row['ids']:board.Delete(items[uid])
            oracle=LocalRouter(board,compact=True,track_width_nm=width,deadline=min(work_stop,time.monotonic()+2),deferred_zone_ids=deferred_zone_ids)
            policy=None
            if ports:
                from .port_policy import PortPolicy
                policy=PortPolicy(board,ports,track_width_nm=width)
            live=[dict(start=xy(t.GetStart()),end=xy(t.GetEnd()),net=t.GetNetname(),layer=t.GetLayer(),width=t.GetWidth()) for t in board.GetTracks() if t.GetClass()=='PCB_TRACK']
            index=ParallelNeighbors(live,eligible_nets=eligible,min_edge_gap=max(oracle.minimum_clearance,173000))
            def score(path):
                length=sum(math.dist(a,z) for a,z in zip(path,path[1:]));coverage=sum(index.coverage(a,z,face,net,width) for a,z in zip(path,path[1:]))
                return shape_score(path,bend_price_nm)+group_strength*max(0,length-coverage)
            candidates={tuple(q) for q in shortcut_candidates(old)}
            refs=[q for q in foreign_chains(live,net,eligible) if q['layer']==face and len(q['path'])<=10]
            refs.sort(key=lambda q:min(math.dist(x,old[0]) for x in q['path'])+min(math.dist(x,old[-1]) for x in q['path']))
            for ref in refs[:5] if group_strength else []:
                if oracle.expired():break
                gap=max(oracle.minimum_clearance,oracle.net_clearances.get(net,0),oracle.net_clearances.get(ref['net'],0));pitch=math.ceil((width+ref['width'])/2+gap+20000)
                for sign in (-1,1):
                    shifted=offset_path(ref['path'],sign*pitch)
                    if shifted is None:continue
                    for middle in follow_spans(shifted,old[0],old[-1]):
                        for left in octile(old[0],middle[0]):
                            for right in octile(middle[-1],old[-1]):
                                q=simplify([*left,*middle[1:],*right[1:]])
                                if valid_shape(q):candidates.add(tuple(q))
            best=score(old);selected=old;old_length=sum(math.dist(a,z) for a,z in zip(old,old[1:]))
            for value,q in sorted((score(q),q) for q in candidates):
                if oracle.expired():break
                if not quality_improves([old],[q],best,value):continue
                if not paths_clear([q],[width],oracle.minimum_clearance):continue
                if sum(math.dist(a,z) for a,z in zip(q,q[1:]))>old_length*1.05+1:continue
                if not _joins_clear(board,q,face,net) or not _pad_entry_clear(board,q,face,net):continue
                if not retained_contacts_clear(board,q,face,net,width,ignored_ids=row['ids'],oracle=oracle):continue
                if policy and any(not policy.accepts_segment(a,z,face,net) for a,z in zip(q,q[1:])):continue
                if not all(oracle.clear(p.SHAPE_SEGMENT(vec(a),vec(z),width),face,net) for a,z in zip(q,q[1:])):continue
                selected=list(q);best=value;break
            adopted=selected!=old;rejection=None
            if adopted:
                oracle.add_path(selected,face,net)
                if board.Zones() and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Single-chain refill failed')
                contacts={frozenset(g) for g in pad_partitions(board,deadline)}
                candidate_bends=copper_metrics(snapshot(board)['tracks'])['bends']
                if contacts!=partitions or candidate_bends>global_bends:
                    rejection='Changed pad contacts' if contacts!=partitions else 'Added global bend'
                    for t in list(board.GetTracks()):
                        if t.m_Uuid.AsString() in set(oracle.added):board.Delete(t)
                    for t in restore:board.Add(t)
                    if board.Zones() and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Single-chain rollback refill failed')
                    if {frozenset(g) for g in pad_partitions(board,deadline)}!=partitions:raise ValueError('Single-chain rollback lost contacts')
                    adopted=False;report['contact_rejections']+=1
                else:global_bends=candidate_bends
            else:
                for t in restore:board.Add(t)
            report['rewrites'].append({'net':net,'old':old,'new':selected if adopted else old,'proposal':selected,'old_ids':row['ids'],'new_ids':oracle.added if adopted else [],'adopted':adopted,'rejection_reason':rejection})
        if board.Zones() and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Polish refill failed')
        after=snapshot(board)
        if before['footprints']!=after['footprints'] or any(after['tracks'].get(u)!=before['tracks'][u] for u in protected):raise ValueError('Polish changed protected source')
        if {frozenset(g) for g in pad_partitions(board,deadline)}!=partitions:raise ValueError('Polish changed an existing numbered-pad partition')
        if time.monotonic()>=deadline:raise TimeoutError('Polish proof deadline exhausted')
        report.update(accepted=True,adopted_chains=sum(q['adopted'] for q in report['rewrites']))
    except (ValueError,TimeoutError) as error:
        rollback();report.update(reason=str(error),adopted_chains=0)
        for row in report['rewrites']:row.update(adopted=False,new=row['old'],new_ids=[])
    except BaseException:
        rollback();raise
    report['elapsed_s']=time.monotonic()-started
    return report


def prune_matrix_contacts(board,*,protected_ids,eligible_nets,deadline):
    """Retire only editable pad-contact copper proven redundant by native CAD."""
    before=snapshot(board);protected=set(protected_ids);eligible=set(eligible_nets);removed=[]
    if not protected<=set(before['tracks']):raise ValueError('Contact-prune protected source is missing')
    if deadline-time.monotonic()<2:return {'removed_ids':[],'all_pad_partitions_exact':True,'reason':'Incumbent retained; no cleanup budget'}
    original={frozenset(g) for g in pad_partitions(board,deadline)}
    pads=[d for f in board.GetFootprints() for d in f.Pads() if d.GetNumber()];candidates=[]
    for t in board.GetTracks():
        uid=t.m_Uuid.AsString()
        if uid in protected or t.IsLocked() or t.GetClass()!='PCB_TRACK' or t.GetNetname() not in eligible:continue
        if any(d.GetNetname()==t.GetNetname() and d.IsOnLayer(t.GetLayer()) and p.SHAPE.Collide(t.GetEffectiveShape(t.GetLayer()),d.GetEffectiveShape(t.GetLayer()),0) for d in pads):candidates.append(uid)
    for uid in sorted(candidates,key=lambda u:(math.dist(before['tracks'][u]['start'],before['tracks'][u]['end']),u)):
        if deadline-time.monotonic()<2:break
        item=next(t for t in board.GetTracks() if t.m_Uuid.AsString()==uid);copy=item.Duplicate();copy.SetUuid(p.KIID(uid));board.Delete(item);okay=False
        try:
            if board.Zones() and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Contact prune refill failed')
            okay={frozenset(g) for g in pad_partitions(board,deadline)}==original
        finally:
            if not okay:
                board.Add(copy)
                if board.Zones() and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Contact restore refill failed')
        if okay:removed.append(uid)
    after=snapshot(board)
    if after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=s for u,s in before['tracks'].items() if u not in removed):raise ValueError('Contact pruning changed retained source')
    return {'removed_ids':removed,'all_pad_partitions_exact':True}
