"""Reconstruct long copper geometry across disposable two-arm layer changes."""
import math,time
from collections import defaultdict
from smilepcb.native_worker import p,snapshot
from .local_router import LocalRouter,xy,vec
from .bundle_polish import extract_chains,retained_contacts_clear,_joins_clear
from .via_junction_polish import _pad_contacts_clear,_via_own_clear
from .visual_polish import _parts,_copy,_refill
from .cross_layer_geometry import assemble_chains
from .visual_geometry import column_paths,column_bypass_paths,feeder_paths,UniformNeighbors,safe_route
from .layer_policy import terminal_layers
from .column_bridges import bridge_carrier
from .polish_geometry import shape_score,shortcut_candidates,octile,valid_shape
from .follow_geometry import foreign_chains
from .role_quality import projected_tracks


def extract_spanning_chains(board,*,eligible_nets,protected_ids,deadline,deferred_zone_ids=()):
    """Only collapse a via when its entire native contact star is modeled."""
    protected=set(protected_ids);nets=set(eligible_nets)
    rows=extract_chains(board,eligible_nets=nets,protected_ids=protected,deadline=deadline,deferred_zone_ids=deferred_zone_ids,max_points=256,min_length_nm=0)
    arms=defaultdict(list);by_net=defaultdict(list);pads=defaultdict(list)
    for row in rows:
        for q in (row['path'][0],row['path'][-1]):arms[row['net'],tuple(q)].append(row)
    for t in board.GetTracks():
        if t.GetNetname() in nets:by_net[t.GetNetname()].append(t)
    for fp in board.GetFootprints():
        for d in fp.Pads():
            if d.GetNetname() in nets:pads[d.GetNetname()].append(d)
    vias=[]
    for net,items in by_net.items():
        for v in items:
            if time.monotonic()>=deadline:raise TimeoutError('Cross-layer extraction deadline')
            uid=v.m_Uuid.AsString()
            if v.GetClass()!='PCB_VIA' or uid in protected or v.IsLocked() or v.GetViaType()!=p.VIATYPE_THROUGH:continue
            pos=xy(v.GetPosition());a=arms.get((net,pos),[])
            if len(a)!=2 or a[0] is a[1] or a[0]['layer']==a[1]['layer'] or a[0]['width']!=a[1]['width']:continue
            ids={u for row in a for u in row['ids']};okay=True
            for face in (p.F_Cu,p.B_Cu):
                if not v.IsOnLayer(face):okay=False;break
                shape=v.GetEffectiveShape(face)
                for d in [*pads[net],*(t for t in items if t.m_Uuid.AsString() not in ids|{uid})]:
                    if d.IsOnLayer(face) and p.SHAPE.Collide(shape,d.GetEffectiveShape(face),0):okay=False;break
                if not okay:break
            if okay:vias.append(dict(id=uid,net=net,position=pos,layers=[p.F_Cu,p.B_Cu]))
    return assemble_chains(rows,vias)


def rebuild_geometry(board,*,nets,preferred_layer,protected_ids,deadline,deferred_zone_ids=(),mode='columns',focus_bounds=None,progress=None,quality_gate=None):
    """Simple carriers replace entire cross-layer chains, with atomic proof.

    Geometry is planned while the original is still present but ignored by
    the obstacle index. Only a fully checked candidate begins a mutation.
    """
    report={'mode':mode,'adopted_chains':0,'rewrites':[],'attempts':[],'quality_vetoes':0}
    if deadline-time.monotonic()<15:return dict(report,reason='No proof reserve; exact incumbent retained')
    if mode not in ('columns','feeders') or preferred_layer not in (p.F_Cu,p.B_Cu):raise ValueError('Unknown geometry role')
    initial=snapshot(board);protected=set(protected_ids)|{u for u,t in initial['tracks'].items() if t['locked']};parts=_parts(board,deadline);stop=deadline-12
    work=extract_spanning_chains(board,eligible_nets=set(nets),protected_ids=protected,deadline=stop,deferred_zone_ids=deferred_zone_ids)
    horizontal=lambda q:sum(abs(b[0]-a[0]) for a,b in zip(q,q[1:]) if a[1]==b[1])
    length=lambda q:sum(math.dist(a,b) for a,b in zip(q,q[1:]))
    if mode=='columns':
        work=[q for q in work if abs(q['path'][-1][1]-q['path'][0][1])>=max(2000000,abs(q['path'][-1][0]-q['path'][0][0]))]
    else:
        work=[q for q in work if length(q['path'])>=2000000 and (focus_bounds is None or any(focus_bounds[0]<=x<=focus_bounds[2] and focus_bounds[1]<=y<=focus_bounds[3] for x,y in (q['path'][0],q['path'][-1])))]
    work.sort(key=lambda q:(-horizontal(q['path']) if mode=='columns' else -length(q['path']),-len(q['ids']),q['net'],q['path']))
    report['eligible_chains']=len(work)
    for index,row in enumerate(work):
        if stop-time.monotonic()<2:break
        net=row['net'];old=row['path'];width=row['width'];ids=set(row['ids']);items={t.m_Uuid.AsString():t for t in board.GetTracks()}
        if not ids<=items.keys():continue
        original=snapshot(board);copies=[_copy(items[u]) for u in ids];old_vias=sum(original['tracks'][u]['class']=='PCB_VIA' for u in ids)
        adopted=False;chosen=None;reason='No improving native-clear complete geometry';checked=0;mutated=False
        try:
            oracle=LocalRouter(board,compact=True,ignored_copper=ids,track_width_nm=width,deadline=min(stop-1,time.monotonic()+5),deferred_zone_ids=deferred_zone_ids)
            other=p.B_Cu if preferred_layer==p.F_Cu else p.F_Cu
            start=terminal_layers(board,old[0],net,row['start_layer']);end=terminal_layers(board,old[-1],net,row['end_layer'])
            live=[dict(t) for u,t in original['tracks'].items() if u not in ids and t['class']=='PCB_TRACK' and t['net']!=net]
            neighbors=UniformNeighbors(live,eligible_nets=set(nets),min_edge_gap=max(oracle.minimum_clearance,198000),min_run_nm=2000000)
            def score(path,legs,vias):
                off=sum(length(leg) for face,leg in legs if face!=preferred_layer)
                coverage=sum(neighbors.coverage(a,b,face,net,width) for face,leg in legs for a,b in zip(leg,leg[1:]))
                value=shape_score(path,3000000)+off*4+len(vias)*400000
                return value+(horizontal(path)*20 if mode=='columns' else 3*max(0,length(path)-coverage))
            old_score=score(old,row['old_legs'],[None]*old_vias)
            if mode=='columns':candidates=column_paths(old[0],old[-1],hints=old)+column_bypass_paths(old[0],old[-1])
            else:
                refs=[q for q in foreign_chains(live,net,set(nets)) if q['layer']==preferred_layer]
                refs.sort(key=lambda q:min(math.dist(a,b) for a in (old[0],old[-1]) for b in q['path']))
                candidates=[*octile(old[0],old[-1]),*shortcut_candidates(old),*feeder_paths(old,refs[:16],net=net,layer=preferred_layer,width=width,gap_nm=neighbors.minimum)]
                candidates=[q for q in candidates if valid_shape(q)]
                # A feeder pass must not undo an already clean V/45 column.
                if abs(old[-1][1]-old[0][1])>=abs(old[-1][0]-old[0][0]) and not horizontal(old):candidates=[q for q in candidates if not horizontal(q)]
            candidates=sorted({tuple(map(tuple,q)) for q in candidates},key=lambda q:(score(q,[(preferred_layer,q)],[]),q))
            for path in candidates:
                if oracle.expired():break
                # A lower bound avoids spending bridge search on no improvement.
                if score(path,[(preferred_layer,path)],[])>=old_score-100000:continue
                checked+=1
                route=bridge_carrier(path,preferred_layer,other,start_layers=start,end_layers=end,secondary_length_factor=5,via_cost_nm=400000,
                    clear=lambda a,b,l:oracle.clear(p.SHAPE_SEGMENT(vec(a),vec(b),width),l,net),
                    via_clear=lambda v:oracle.via_clear(v,net) and _via_own_clear(oracle,v,net,oracle.via_width),
                    via_pair_clear=lambda a,b:math.dist(a,b)>=oracle.via_width+oracle.minimum_clearance and oracle.via_pair_clear(a,b),deadline=oracle.deadline)
                if route is None or not safe_route(route,width,oracle.via_width):continue
                if score(path,route['legs'],route['vias'])>=old_score-100000:continue
                if any(not _joins_clear(board,leg,face,net,ids) or not _pad_contacts_clear(board,leg,face,net,width,oracle) or not retained_contacts_clear(board,leg,face,net,width,ignored_ids=ids,oracle=oracle) for face,leg in route['legs']):continue
                if quality_gate is not None and not quality_gate(original['tracks'],projected_tracks(original['tracks'],ids,[(face,net,width,leg) for face,leg in route['legs']])):
                    report['quality_vetoes']+=1;continue
                chosen=dict(route,carrier=list(path));break
            if chosen is not None and not oracle.expired():
                mutated=True
                for u in ids:board.Delete(items[u])
                oracle.deadline=stop
                for face,leg in chosen['legs']:oracle.add_path(leg,face,net)
                for v in chosen['vias']:oracle.add_via(v,net)
                _refill(board);adopted=_parts(board,deadline)==parts
                if adopted and quality_gate is not None:adopted=quality_gate(original['tracks'],snapshot(board)['tracks'])
                if not adopted:reason='Rejected changed whole-board numbered-pad contacts'
            if mutated and not adopted:
                for t in list(board.GetTracks()):
                    if t.m_Uuid.AsString() not in original['tracks']:board.Delete(t)
                for t in copies:board.Add(t)
                _refill(board)
                if snapshot(board)!=original or _parts(board,deadline)!=parts:raise ValueError('Geometry rollback changed source')
            if adopted:
                after=snapshot(board)
                if after['footprints']!=original['footprints'] or any(after['tracks'].get(u)!=t for u,t in original['tracks'].items() if u not in ids):raise ValueError('Geometry changed retained objects')
                report['adopted_chains']+=1;report['rewrites'].append({'net':net,'old':old,'new':chosen['carrier'],'old_legs':row['old_legs'],'legs':chosen['legs'],'vias':chosen['vias'],'old_ids':row['ids'],'new_ids':list(oracle.added),'old_score':old_score,'new_score':score(chosen['carrier'],chosen['legs'],chosen['vias'])})
        except BaseException as error:
            if mutated:
                for t in list(board.GetTracks()):
                    if t.m_Uuid.AsString() not in original['tracks']:board.Delete(t)
                live_ids={t.m_Uuid.AsString() for t in board.GetTracks()}
                for t in copies:
                    if t.m_Uuid.AsString() not in live_ids:board.Add(t)
                _refill(board)
                if snapshot(board)!=original:raise ValueError('Geometry exception rollback changed source')
            if isinstance(error,TimeoutError):reason='Candidate expiry; exact geometry retained';adopted=False
            else:raise
        report['attempts'].append({'net':net,'checked':checked,'old_objects':len(ids),'old_horizontal_nm':horizontal(old),'adopted':adopted,'reason':None if adopted else reason})
        if progress:progress({'stage':mode,'index':index+1,'total':len(work),'accepted':report['adopted_chains']})
    if _parts(board,deadline)!=parts:raise ValueError('Geometry final contacts changed')
    report['all_pad_partitions_exact']=True
    return report
