"""Model-assigned preferred layers, with native access and atomic migration."""
import math,time
from smilepcb.native_worker import p,snapshot
from .local_router import LocalRouter,vec,xy
from .bundle_polish import extract_chains,retained_contacts_clear,_joins_clear
from .via_junction_polish import _pad_contacts_clear,_via_own_clear
from .visual_polish import _parts,_copy,_refill
from .visual_geometry import safe_route
from .column_bridges import bridge_carrier


def terminal_layers(board,position,net,original_face):
    """Reuse actual through-hole/via access; an SMD pad remains on its face."""
    result={original_face};position=tuple(position)
    for item in board.GetTracks():
        if item.GetClass()=='PCB_VIA' and item.GetNetname()==net and xy(item.GetPosition())==position:
            result.update(layer for layer in (p.F_Cu,p.B_Cu) if item.IsOnLayer(layer))
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname()!=net or not pad.IsOnLayer(p.F_Cu) or not pad.IsOnLayer(p.B_Cu):continue
            if xy(pad.GetPosition())==position:result.update((p.F_Cu,p.B_Cu))
    return result


def migrate_layers(board,*,assignments,protected_ids,deadline,deferred_zone_ids=(),sweeps=2,groups=None,progress=None):
    """Move unlocked chain copper to its assigned side, retaining pad contacts."""
    report={'adopted_chains':0,'rewrites':[],'attempts':[],'sweeps':0,'phase_totals':[]}
    if deadline-time.monotonic()<15:return dict(report,reason='No proof reserve; incumbent retained')
    if not assignments or any(layer not in (p.F_Cu,p.B_Cu) for layer in assignments.values()):raise ValueError('Expected physical two-layer assignments')
    if not 1<=sweeps<=4:raise ValueError('Expected one to four policy sweeps')
    before=snapshot(board);protected=set(protected_ids)|{u for u,t in before['tracks'].items() if t['locked']}
    if not protected<=before['tracks'].keys():raise ValueError('Missing protected source')
    parts=_parts(board,deadline);stop=deadline-12
    groups=[('all',set(assignments))] if groups is None else [(name,set(nets)) for name,nets in groups if nets]
    if set().union(*(nets for _,nets in groups))!=set(assignments):raise ValueError('Incomplete policy role groups')
    for sweep in range(sweeps):
        count=report['adopted_chains']
        for index,(label,nets) in enumerate(groups):
            remaining=(sweeps-sweep)*len(groups)-index
            phase_stop=time.monotonic()+max(0,stop-time.monotonic())/remaining
            if phase_stop-time.monotonic()<2:break
            if progress:progress({'stage':label,'sweep':sweep,'accepted':report['adopted_chains'],'attempted':len(report['attempts'])})
            work=extract_chains(board,eligible_nets=nets,protected_ids=protected,deadline=phase_stop,deferred_zone_ids=deferred_zone_ids,max_points=128,min_length_nm=500000)
            work=[q for q in work if q['layer']!=assignments[q['net']]]
            work.sort(key=lambda q:(-sum(math.dist(a,b) for a,b in zip(q['path'],q['path'][1:])),q['net'],q['path']))
            phase={'role':label,'sweep':sweep,'wrong_side_chains':len(work),'adopted':0};report['phase_totals'].append(phase)
            for row in work:
                if phase_stop-time.monotonic()<1.5:break
                old=row['path'];net=row['net'];face=row['layer'];target=assignments[net];width=row['width'];ids=set(row['ids']);original=snapshot(board);items={t.m_Uuid.AsString():t for t in board.GetTracks()}
                if not ids<=items.keys():continue
                copies=[_copy(items[u]) for u in ids]
                start_layers=terminal_layers(board,old[0],net,face);end_layers=terminal_layers(board,old[-1],net,face)
                for u in ids:board.Delete(items[u])
                adopted=False;route=None;reason='No improving native-clear assigned-side route';old_length=sum(math.dist(a,b) for a,b in zip(old,old[1:]))
                try:
                    oracle=LocalRouter(board,compact=True,track_width_nm=width,deadline=min(phase_stop-1,time.monotonic()+2),deferred_zone_ids=deferred_zone_ids)
                    other=p.B_Cu if target==p.F_Cu else p.F_Cu
                    route=bridge_carrier(old,target,other,start_layers=start_layers,end_layers=end_layers,secondary_length_factor=8,via_cost_nm=500000,
                        clear=lambda a,b,l:oracle.clear(p.SHAPE_SEGMENT(vec(a),vec(b),width),l,net),
                        via_clear=lambda v:oracle.via_clear(v,net) and _via_own_clear(oracle,v,net,oracle.via_width),
                        via_pair_clear=lambda a,b:math.dist(a,b)>=oracle.via_width+oracle.minimum_clearance and oracle.via_pair_clear(a,b),deadline=oracle.deadline)
                    if route is not None:
                        preferred_length=sum(math.dist(a,b) for l,path in route['legs'] if l==target for a,b in zip(path,path[1:]))
                        allowed=preferred_length>=250000 and safe_route(route,width,oracle.via_width)
                        allowed=allowed and all(_joins_clear(board,path,l,net,ids) and _pad_contacts_clear(board,path,l,net,width,oracle) and retained_contacts_clear(board,path,l,net,width,ignored_ids=ids,oracle=oracle) for l,path in route['legs'])
                        if allowed and not oracle.expired():
                            oracle.deadline=phase_stop
                            for l,path in route['legs']:oracle.add_path(path,l,net)
                            for v in route['vias']:oracle.add_via(v,net)
                            _refill(board)
                            adopted=_parts(board,deadline)==parts
                            if not adopted:reason='Assigned-side route changed whole-board contacts'
                    if not adopted:
                        for t in list(board.GetTracks()):
                            if t.m_Uuid.AsString() not in original['tracks']:board.Delete(t)
                        for t in copies:board.Add(t)
                        _refill(board)
                        if snapshot(board)!=original or _parts(board,deadline)!=parts:raise ValueError('Policy rollback lost source or contacts')
                    else:
                        after=snapshot(board)
                        if after['footprints']!=original['footprints'] or any(after['tracks'].get(u)!=t for u,t in original['tracks'].items() if u not in ids):raise ValueError('Policy changed retained source')
                        report['rewrites'].append({'net':net,'role':label,'from_layer':face,'preferred_layer':target,'old':old,'legs':route['legs'],'vias':route['vias'],'old_ids':row['ids'],'new_ids':oracle.added,'preferred_length_nm':preferred_length,'old_length_nm':old_length})
                        report['adopted_chains']+=1;phase['adopted']+=1
                except BaseException as error:
                    for t in list(board.GetTracks()):
                        if t.m_Uuid.AsString() not in original['tracks']:board.Delete(t)
                    live={t.m_Uuid.AsString() for t in board.GetTracks()}
                    for t in copies:
                        if t.m_Uuid.AsString() not in live:board.Add(t)
                    _refill(board)
                    if snapshot(board)!=original:raise ValueError('Policy exception rollback changed source')
                    if isinstance(error,TimeoutError):
                        adopted=False;reason='Candidate deadline; exact chain restored'
                    else:raise
                report['attempts'].append({'net':net,'role':label,'sweep':sweep,'adopted':adopted,'reason':None if adopted else reason})
                if progress and (adopted or len(report['attempts'])%10==0):progress({'stage':label,'sweep':sweep,'accepted':report['adopted_chains'],'attempted':len(report['attempts'])})
        report['sweeps']+=1
        if report['adopted_chains']==count:break
    if _parts(board,deadline)!=parts:raise ValueError('Policy final contacts changed')
    report.update(accepted=True,all_pad_partitions_exact=True)
    return report
