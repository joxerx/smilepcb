"""Transactional completed-board direction and duplicate refinement."""
import math,time
from smilepcb.native_worker import p,snapshot
from .local_router import LocalRouter,vec
from .completion import pad_partitions
from .bundle_polish import extract_chains,retained_contacts_clear,_joins_clear
from .via_junction_polish import _pad_contacts_clear,_via_own_clear
from .column_bridges import bridge_carrier
from .visual_geometry import column_paths,column_bypass_paths,duplicate_ids,UniformNeighbors,feeder_paths,safe_route
from .polish_geometry import shape_score,shortcut_candidates
from .follow_geometry import foreign_chains


def _parts(board,deadline):return {frozenset(g) for g in pad_partitions(board,deadline)}
def _copy(t):
    q=t.Duplicate();q.SetUuid(p.KIID(t.m_Uuid.AsString()));return q

def _refill(board):
    board.BuildConnectivity()
    if board.Zones() and not p.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('Visual refinement refill failed')


def prune_duplicates(board,*,protected_ids,deadline):
    before=snapshot(board);parts=_parts(board,deadline);ids=duplicate_ids(before['tracks'],protected_ids)
    copies=[];items={t.m_Uuid.AsString():t for t in board.GetTracks()}
    try:
        for uid in ids:copies.append(_copy(items[uid]));board.Delete(items[uid])
        _refill(board)
        if _parts(board,deadline)!=parts:raise ValueError('Duplicate pruning changed contacts')
        after=snapshot(board)
        if after['footprints']!=before['footprints'] or any(after['tracks'].get(u)!=t for u,t in before['tracks'].items() if u not in ids):raise ValueError('Duplicate pruning changed retained source')
    except BaseException:
        for t in copies:board.Add(t)
        _refill(board)
        if snapshot(board)!=before:raise ValueError('Duplicate rollback changed source')
        raise
    return {'removed_ids':ids,'pad_partitions_exact':True}


def polish_columns(board,*,column_nets,protected_ids,deadline,deferred_zone_ids=(),minimum_span_nm=5000000,mode="columns",focus_bounds=None):
    """Search V/45/V backbones; horizontal MCU feeders are a separate role."""
    if deadline-time.monotonic()<15:return {'accepted':False,'adopted_chains':0,'rewrites':[],'attempts':[],'reason':'No proof reserve; incumbent retained'}
    before=snapshot(board);parts=_parts(board,deadline);protected=set(protected_ids);report={'accepted':True,'rewrites':[],'attempts':[]}
    stop=deadline-12;work=extract_chains(board,eligible_nets=set(column_nets),protected_ids=protected,deadline=stop,deferred_zone_ids=deferred_zone_ids,max_points=128,min_length_nm=minimum_span_nm)
    if mode=='columns':work=[q for q in work if abs(q['path'][-1][1]-q['path'][0][1])>=max(minimum_span_nm,abs(q['path'][-1][0]-q['path'][0][0]))]
    else:work=[q for q in work if focus_bounds is None or any(focus_bounds[0]<=p[0]<=focus_bounds[2] and focus_bounds[1]<=p[1]<=focus_bounds[3] for p in (q['path'][0],q['path'][-1]))]
    horizontal=lambda path:sum(abs(b[0]-a[0]) for a,b in zip(path,path[1:]) if a[1]==b[1])
    work.sort(key=lambda q:(-horizontal(q['path']),-len(q['path']),q['net'],q['path']))
    if mode=='feeders':work.sort(key=lambda q:(-sum(math.dist(a,b) for a,b in zip(q['path'],q['path'][1:])),q['net'],q['path']))
    report['backbone_chains']=len(work);report['horizontal_before_nm']=sum(horizontal(q['path']) for q in work)
    removed=0
    for row in work:
        if time.monotonic()>=stop:break
        old=row['path'];net=row['net'];face=row['layer'];width=row['width'];ids=set(row['ids']);items={t.m_Uuid.AsString():t for t in board.GetTracks()}
        if not ids<=items.keys() or mode=='columns' and not horizontal(old) and len(old)<=3:continue
        original=snapshot(board);copies=[_copy(items[u]) for u in ids]
        for u in ids:board.Delete(items[u])
        adopted=False;chosen=None;rejection='No improving native-clear replacement';checked=0
        try:
            oracle=LocalRouter(board,compact=True,track_width_nm=width,deadline=min(stop,time.monotonic()+4),deferred_zone_ids=deferred_zone_ids)
            live=[dict(t) for t in original['tracks'].values() if t['class']=='PCB_TRACK' and t['net']!=net]
            neighbors=UniformNeighbors(live,eligible_nets=set(column_nets),min_edge_gap=max(oracle.minimum_clearance,173000)+(25000 if mode=='feeders' else 0),min_run_nm=2000000)
            def score(q):
                length=sum(math.dist(a,b) for a,b in zip(q,q[1:]));covered=sum(neighbors.coverage(a,b,face,net,width) for a,b in zip(q,q[1:]))
                return shape_score(q,2000000)+(3 if mode=='feeders' else 1)*max(0,length-covered)
            candidates=column_paths(old[0],old[-1],hints=old)+column_bypass_paths(old[0],old[-1])
            if mode=='feeders':
                refs=[q for q in foreign_chains(live,net,set(column_nets)) if q['layer']==face]
                refs.sort(key=lambda q: min(math.dist(a,b) for a in (old[0],old[-1]) for b in q['path']))
                candidates=shortcut_candidates(old)+feeder_paths(old,refs[:12],net=net,layer=face,width=width,gap_nm=neighbors.minimum)
            for q in sorted(candidates,key=lambda q:(score(q),q)):
                if oracle.expired():break
                if (mode=='feeders' or not horizontal(old)) and score(q)>=score(old)-100000:continue
                if sum(math.dist(a,b) for a,b in zip(q,q[1:]))>sum(math.dist(a,b) for a,b in zip(old,old[1:]))*1.2:continue
                checked+=1
                other=p.F_Cu if face==p.B_Cu else p.B_Cu
                route=bridge_carrier(q,face,other,
                    clear=lambda a,b,l:oracle.clear(p.SHAPE_SEGMENT(vec(a),vec(b),width),l,net),
                    via_clear=lambda v:oracle.via_clear(v,net) and _via_own_clear(oracle,v,net,oracle.via_width),
                    via_pair_clear=lambda a,b:math.dist(a,b)>=oracle.via_width+oracle.minimum_clearance and oracle.via_pair_clear(a,b),
                    deadline=oracle.deadline)
                if route is None or not safe_route(route,width,oracle.via_width):continue
                if mode=='feeders':
                    length=sum(math.dist(a,b) for l,leg in route['legs'] for a,b in zip(leg,leg[1:]));coverage=sum(neighbors.coverage(a,b,l,net,width) for l,leg in route['legs'] for a,b in zip(leg,leg[1:]))
                    actual=shape_score(q,2000000)+3*max(0,length-coverage)+2000000*len(route['vias'])
                    if actual>=score(old)-100000:continue
                if any(not _joins_clear(board,leg,layer,net,ids) or not _pad_contacts_clear(board,leg,layer,net,width,oracle) or not retained_contacts_clear(board,leg,layer,net,width,ignored_ids=ids,oracle=oracle) for layer,leg in route['legs']):continue
                chosen={'carrier':q,**route};break
            if chosen is not None:
                for layer,leg in chosen['legs']:oracle.add_path(leg,layer,net)
                for v in chosen['vias']:oracle.add_via(v,net)
                _refill(board)
                adopted=_parts(board,deadline)==parts
                if not adopted:rejection='Replacement changed whole-board contacts'
            if not adopted:
                current={t.m_Uuid.AsString():t for t in board.GetTracks()}
                for u,t in current.items():
                    if u not in original['tracks']:board.Delete(t)
                for t in copies:board.Add(t)
                _refill(board)
                if snapshot(board)!=original or _parts(board,deadline)!=parts:raise ValueError('Column rollback changed source')
            else:
                after=snapshot(board)
                if after['footprints']!=original['footprints'] or any(after['tracks'].get(u)!=t for u,t in original['tracks'].items() if u not in ids):raise ValueError('Column rewrite changed retained geometry')
                report['rewrites'].append({'net':net,'layer':face,'old':old,'new':chosen['carrier'],'legs':chosen['legs'],'vias':chosen['vias'],'old_ids':row['ids'],'new_ids':oracle.added,'adopted':True})
                removed+=horizontal(old)-horizontal(chosen['carrier'])
        except BaseException:
            for t in list(board.GetTracks()):
                if t.m_Uuid.AsString() not in original['tracks']:board.Delete(t)
            live={t.m_Uuid.AsString() for t in board.GetTracks()}
            for t in copies:
                if t.m_Uuid.AsString() not in live:board.Add(t)
            _refill(board)
            if snapshot(board)!=original:raise ValueError('Column exception rollback changed source')
            raise
        report['attempts'].append({'net':net,'layer':face,'old_horizontal_nm':horizontal(old),'adopted':adopted,'checked':checked,'reason':None if adopted else rejection})
    if _parts(board,deadline)!=parts:raise ValueError('Column refinement changed final contacts')
    report.update(horizontal_after_nm=report['horizontal_before_nm']-removed,adopted_chains=len(report['rewrites']))
    return report
