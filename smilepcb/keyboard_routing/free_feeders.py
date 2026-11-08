"""Bounded actual-pad routing on a caller-certified headless matrix scratch board.

The caller supplies the immutable retained frame and owns complete native proof,
reload, publication and rollback. This helper reports observations, never matrix
acceptance. No matrix launch is invented or reused after its head is released.
"""
from copy import deepcopy
import math
import time

from smilepcb.keyboard_phases import ordered_nets


def _check_frame(board,retained,nets):
    from smilepcb.native_worker import snapshot
    live=snapshot(board)
    if (live['footprints']!=retained['footprints'] or
            any(live['tracks'].get(uid)!=shape for uid,shape in retained['tracks'].items())):
        raise ValueError('Free feeders changed retained copper or physical pads')
    mutable={uid:shape for uid,shape in live['tracks'].items() if uid not in retained['tracks']}
    if any(shape['net'] not in nets or shape['locked'] or shape['class'] not in ('PCB_TRACK','PCB_VIA')
           for shape in mutable.values()):
        raise ValueError('Free feeders require unlocked matrix-only mutable copper')
    return live


def _parallel_cost(board,router,nets):
    from smilepcb.native_worker import snapshot
    from .parallel_neighbors import ParallelNeighbors
    tracks=snapshot(board)['tracks'].values()
    index=ParallelNeighbors([q for q in tracks if q['class']=='PCB_TRACK'],eligible_nets=nets,
        min_edge_gap=max(router.minimum_clearance,173000),max_edge_gap=1000000,min_run_nm=0)
    def penalty(shape,layer,net,via):
        if via:return 0.
        segment=shape.GetSeg()
        return index.surcharge((segment.A.x,segment.A.y),(segment.B.x,segment.B.y),
            layer,net,router.track_width,.9)
    return penalty


def route_free_feeders(board,cfg,retained,*,groups,fixed_ports,deadline):
    """Route every configured matrix net under one absolute routing/repair clock."""
    started=time.monotonic()
    if type(deadline) not in (int,float) or not math.isfinite(deadline):
        raise ValueError('Free feeders require a finite absolute deadline')
    phases=dict(ordered_nets(cfg));nets=set(phases['rows']+phases['columns'])
    if (not isinstance(groups,(list,tuple)) or not groups or
            any(not isinstance(group,(list,tuple)) or not group or
                any(not isinstance(net,str) for net in group) for group in groups)):
        raise ValueError('Free-feeder groups must partition the configured matrix')
    listed=[net for group in groups for net in group]
    if len(listed)!=len(set(listed)) or set(listed)!=nets:
        raise ValueError('Free-feeder groups must partition the configured matrix')
    _check_frame(board,retained,nets)
    from .interface_launches import reserve_launches,current_launch_inventory,interface_identity
    pads={q['uuid']:q for f in retained['footprints'].values() for q in f['pads']}
    keys=set();owned=set()
    for port in fixed_ports:
        ids=port['added_ids'];key=interface_identity(port);net=port['net']
        if (net in nets or not key or key in keys or not ids or len(ids)!=len(set(ids)) or owned.intersection(ids) or
                any(uid not in retained['tracks'] or retained['tracks'][uid]['net']!=net for uid in ids) or
                any(uid not in pads or pads[uid]['net']!=net for uid in key)):
            raise ValueError('Free-feeder fixed interfaces lack exact retained ownership')
        keys.add(key);owned.update(ids)
    fixed=deepcopy(fixed_ports);protected=set(retained['tracks'])
    repair_seconds=min(45.,max(0.,deadline-started)/2);routing_stop=deadline-repair_seconds
    report={'routing_history':[],'repair_history':[],'groups':[],'remaining':None,
        'remaining_basis':'not_observed','remaining_observed_at':None,
        'routing_deadline':routing_stop,'repair_deadline':deadline,'repair_reserved_s':repair_seconds}
    from .matrix import KeyboardRouter
    from .native_graph import route_components
    from .feeder_repair import matrix_pad_state,repair_feeders
    def assess(stop):
        if time.monotonic()>=stop:raise TimeoutError('Free-feeder native assessment deadline')
        state=matrix_pad_state(board,nets,stop)
        report.update(remaining=deepcopy(state['missing']),remaining_basis='last_completed_native_graph',
                      remaining_observed_at=time.monotonic())
        return state
    outer=current_launch_inventory()
    resolved=outer.resolved&keys if outer is not None else set()
    reserved=outer.repair_reserved&keys if outer is not None else set()
    try:
        with reserve_launches(fixed,resolved=resolved,repair_reserved=reserved):
            try:
                for ordinal,group in enumerate(groups):
                    state=assess(routing_stop)
                    pending=[sum(net in state['missing'] for net in rest) for rest in groups[ordinal:]]
                    if not sum(pending):break
                    if not pending[0]:continue
                    now=time.monotonic();end=min(routing_stop,now+max(0.,routing_stop-now)*pending[0]/sum(pending))
                    group_report={'ordinal':ordinal,'nets':list(group),'deadline':end,'pending_group_sizes':pending}
                    report['groups'].append(group_report)
                    try:
                        for grid,allowance in ((500000,2),(125000,4)):
                            if time.monotonic()>=end:break
                            state=assess(end)
                            for net in group:
                                if net not in state['missing'] or time.monotonic()>=end:continue
                                tick=time.monotonic()
                                router=KeyboardRouter(board,deadline=min(end,tick+allowance),compact=True,
                                    track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000)
                                router.grid_step=grid;router.search_budget_s=allowance;router.connection_budget_s=allowance
                                router.search_expansion_limit=200000;router.heuristic_weight=3
                                router.via_cost_nm=1000000;router.layer_change_heuristic_nm=0
                                router.routing_penalty=_parallel_cost(board,router,nets)
                                row={'net':net,'grid_nm':grid,'deadline':router.deadline,'connections':0}
                                report['routing_history'].append(row)
                                try:
                                    if not router.expired():
                                        row['connections']=route_components(board,router,{net},finer_grid_steps=(125000,) if grid==500000 else (),
                                            **({'refresh_components':True} if cfg.get('routing_preparation',{}).get('unified_controller_plan') is True else {}))
                                finally:
                                    row.update(elapsed_s=time.monotonic()-tick,diagnostic=getattr(router,'graph_diagnostic',{}))
                                    _check_frame(board,retained,nets)
                    except TimeoutError as error:
                        group_report['reason']=str(error)
                        _check_frame(board,retained,nets)
                if time.monotonic()<routing_stop:assess(routing_stop)
            except TimeoutError as error:
                report['routing_reason']=str(error)
                _check_frame(board,retained,nets)
            if report['remaining'] is None or report['remaining']:
                if time.monotonic()<deadline:
                    try:
                        report['repair_history']=repair_feeders(board,nets,protected,deadline=deadline,
                            ports=fixed,source_ports=fixed,target_order=listed)
                    except TimeoutError as error:report['repair_reason']=str(error)
                    finally:_check_frame(board,retained,nets)
            if time.monotonic()<deadline:
                try:assess(deadline)
                except TimeoutError as error:report['assessment_reason']=str(error)
    finally:
        report.update(elapsed_s=time.monotonic()-started,deadline_exhausted=time.monotonic()>=deadline)
    return report
