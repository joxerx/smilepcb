"""Plan owned source escapes and conditional plane branches as one transaction.

Filled connectivity is a commit guard. Saved reload and fresh ERC/DRC remain
caller responsibilities; no file is read or written by this module.
"""
from copy import deepcopy
import time
from .electrical_partitions import conductor_groups


def _check(deadline):
    if time.monotonic()>=deadline:raise TimeoutError('Finalized bundle deadline exhausted')


def _zone_ids(board):
    return sorted(q.m_Uuid.AsString() for q in board.Zones() if not q.GetIsRuleArea())


def _nets(board):
    return {q.GetNetname() for q in [*board.GetTracks(),*(d for f in board.GetFootprints() for d in f.Pads()),*board.Zones()] if q.GetNetname()}


def plan_finalized_group(board,group,*,bounds,preferred_layer,protected_ids,deadline,
                         candidate_axes=None,plane_witness_ids=(),candidate_gate=None,candidate_score=None,
                         source_mode='owned-prefix',source_strategy='sequential'):
    """Plan without changing the input board; witnesses never authorize commit."""
    from smilepcb.native_worker import snapshot
    from .obstacle_bundles import plan_obstacle_group
    from .source_prefixes import source_prefix_variant
    from .plane_branches import candidate_plane_branches
    from .exact_visibility import search
    from .joint_repair_contract import NativeRepairResult
    before=snapshot(board);zones=_zone_ids(board);held=[];ownership=None;prefixes=[]
    result=dict(planned=False,removed_ids=[],source_snapshot=before,deferred_zone_ids=zones)
    try:
        _check(deadline)
        if not zones:raise ValueError('Finalized bundle requires ordinary copper zones')
        if source_strategy not in ('sequential','joint','staged-joint'):raise ValueError('Unknown source strategy')
        if source_strategy=='staged-joint' and source_mode!='direct':raise ValueError('Staged joint requires source_mode=direct')
        if source_mode not in ('owned-prefix','direct'):raise ValueError('Unknown finalized source mode')
        witnesses=set(plane_witness_ids)
        if witnesses:
            if deadline-time.monotonic()<10:raise TimeoutError('No plane ownership proof reserve')
            ownership=candidate_plane_branches(board,witnesses,protected_ids=protected_ids)
            if ownership is None:raise ValueError('Collision witnesses do not own a removable plane branch')
            branch_ids=set(ownership['removed_ids'])
            if branch_ids&{u for row in group for u in row['ids']}:raise ValueError('Plane branch overlaps signal ownership')
            items={q.m_Uuid.AsString():q for q in board.GetTracks()}
            if not branch_ids<=items.keys():raise ValueError('Plane branch source changed')
            for uid in sorted(branch_ids):
                item=items[uid];held.append(item);board.Remove(item)
        selected=[]
        for row in group:
            _check(deadline)
            variant=source_prefix_variant(board,row,protected_ids=protected_ids) if source_mode=='owned-prefix' else None
            selected.append(deepcopy(row) if variant is None else variant)
            if variant is not None:prefixes.append(dict(net=row['net'],**deepcopy(variant['source_prefix_witness'])))
        _check(deadline)
        result=plan_obstacle_group(board,selected,bounds=bounds,preferred_layer=preferred_layer,
            protected_ids=protected_ids,deadline=deadline,candidate_axes=candidate_axes,attachment_search=search,
            deferred_zone_ids=zones,candidate_gate=candidate_gate,candidate_score=candidate_score,source_strategy=source_strategy)
        _check(deadline)
        if result.get('planned'):
            result['removed_ids']=sorted(set(result['removed_ids'])|{q.m_Uuid.AsString() for q in held})
    except (ValueError,TimeoutError) as error:
        result.update(planned=False,removed_ids=[],reason=str(error),accepted_source_strategy=None)
    finally:
        live={q.m_Uuid.AsString() for q in board.GetTracks()}
        for item in held:
            if item.m_Uuid.AsString() not in live:board.Add(item)
        if snapshot(board)!=before or _zone_ids(board)!=zones:
            raise ValueError('Finalized planning failed to restore the exact source')
    result.update(source_snapshot=before,deferred_zone_ids=zones,source_readonly=True,
                  plane_ownership=ownership,source_prefixes=prefixes,source_mode=source_mode,source_strategy=source_strategy)
    return NativeRepairResult(result).retain_native(board,held)


def commit_finalized_group(board,plan,*,protected_ids,deadline,quality_gate=None):
    """Commit only if every original filled conductor component is preserved."""
    from smilepcb.native_worker import snapshot
    from .obstacle_bundles import commit_obstacle_group
    from .visual_polish import _copy,_refill
    from .native_graph import copper_graph
    from .joint_repair_contract import NativeRepairResult,component_obligations,require_components
    before=snapshot(board);zones=_zone_ids(board);copies={}
    def rollback():
        for item in list(board.GetTracks()):
            if item.m_Uuid.AsString() not in before['tracks']:board.Delete(item)
        live={q.m_Uuid.AsString() for q in board.GetTracks()}
        for uid,item in copies.items():
            if uid not in live:board.Add(_copy(item))
        _refill(board)
        if snapshot(board)!=before or _zone_ids(board)!=zones:
            raise ValueError('Finalized bundle rollback lost the original source')
    try:
        _check(deadline)
        if not plan.get('planned'):raise ValueError('No complete finalized plan')
        if before!=plan['source_snapshot'] or not zones or zones!=sorted(plan.get('deferred_zone_ids',())):
            raise ValueError('Finalized bundle source or zones changed')
        removed=set(plan['removed_ids']);items={q.m_Uuid.AsString():q for q in board.GetTracks()}
        if not removed<=items.keys() or removed&set(protected_ids):raise ValueError('Finalized workset is stale or protected')
        if any(items[u].IsLocked() or items[u].GetNetname() in {'USB_DP','USB_DM'} for u in removed):
            raise ValueError('Finalized workset includes locked or USB copper')
        copies={u:_copy(items[u]) for u in removed}
        _refill(board);_check(deadline)
        original_graph=copper_graph(board,_nets(board),deadline,include_zones=True)
        obligations=component_obligations(conductor_groups(original_graph),removed)
        _check(deadline)
        result=commit_obstacle_group(board,plan,protected_ids=protected_ids,deadline=deadline,quality_gate=quality_gate)
        if not result.get('accepted'):
            if snapshot(board)!=before:rollback()
            return NativeRepairResult(dict(result,source_exact=True)).retain_native(board,copies)
        _check(deadline);_refill(board);_check(deadline)
        after=snapshot(board);added=set(after['tracks'])-set(before['tracks'])
        if (set(before['tracks'])-set(after['tracks'])!=removed or before['footprints']!=after['footprints']
                or any(after['tracks'].get(u)!=q for u,q in before['tracks'].items() if u not in removed)
                or _zone_ids(board)!=zones):raise ValueError('Finalized commit changed retained source')
        final_graph=copper_graph(board,_nets(board),deadline,include_zones=True)
        require_components(obligations,conductor_groups(final_graph),added=added);_check(deadline)
        return NativeRepairResult(dict(result,conductor_components_preserved=True,new_ids=sorted(added),
            requires_saved_erc_drc=True)).retain_native(board,copies,original_graph,final_graph)
    except (ValueError,TimeoutError) as error:
        rollback()
        return NativeRepairResult(dict(accepted=False,reason=str(error),source_exact=True)).retain_native(board,copies)
    except BaseException:
        rollback();raise
