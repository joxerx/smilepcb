"""Native provisional saves confined to newly allocated disposable candidates.

This bridge cannot claim final acceptance. The ordinary keyboard_native
completion dispatch retains its strict improvement and partition guards.
"""
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from smilepcb.native_worker import p,snapshot
from smilepcb.keyboard_native import validate,pad_gaps
from smilepcb.keyboard_lookahead import provisional_step,lookahead_limits
from smilepcb.keyboard_routing.completion import repair_candidate,pad_partitions,partitions_preserved,require_provisional_frame
from smilepcb.keyboard_routing.safety import require_via_land_clearance


def owned_board(request):
    """Check the allocation marker and exact hop path before loading a board."""
    from smilepcb.keyboard_recovery import _read_interface_proof
    raw=Path(request['board']);root=Path(request['trial_root'])
    elastic=request.get('elastic',False);max_depth,_=lookahead_limits(elastic)
    if not raw.is_absolute() or not root.is_absolute() or raw.resolve()!=raw or root.resolve()!=root or not root.name.startswith('lookahead-'):
        raise ValueError('Trial must use an allocated disposable root')
    if raw not in [root/('hop-'+str(depth))/'project/keyboard.kicad_pcb' for depth in range(max_depth)]:
        raise ValueError('Provisional save is outside the owned candidate')
    marker=_read_interface_proof(root/'.smilepcb-lookahead-owner.json')
    if not isinstance(marker,dict) or marker.get('token')!=request.get('owner_token') or not isinstance(marker.get('token'),str):
        raise ValueError('Trial allocation owner does not match')
    marker_mode=marker.get('elastic',False);lookahead_limits(marker_mode)
    marker_ceiling=marker.get('gap_ceiling')
    if marker_mode:
        if type(marker_ceiling) is not int or not 1<=marker_ceiling<=8:
            raise ValueError('Invalid allocation gap ceiling')
    elif marker_ceiling is not None:raise ValueError('Conservative allocation has a gap override')
    if marker_mode is not elastic or marker_ceiling!=request.get('gap_ceiling'):
        raise ValueError('Trial exploration policy does not match its allocation')
    reference=Path(marker.get('source_board',''))
    if not reference.is_absolute() or reference.resolve()!=reference or root in reference.parents or not reference.is_file() or not raw.is_file():
        raise ValueError('Invalid original or disposable board')
    return raw,reference


def run(request):
    from smilepcb.routing_quality import using_quality_profile
    with using_quality_profile(request.get('routing_quality','legacy')) as quality:
        result=_run(request)
        result['routing_quality']=quality.name
        return result


def _run(request):
    path,reference=owned_board(request)
    elastic=request.get('elastic',False);max_depth,_=lookahead_limits(elastic)
    ceiling=request.get('gap_ceiling')
    if elastic:
        if type(ceiling) is not int or not 1<=ceiling<=8:raise ValueError('Invalid trial gap ceiling')
    elif ceiling is not None:raise ValueError('Conservative trials cannot override the gap bound')
    if request.get('operation')=='provisional' and (type(request.get('depth')) is not int or not 0<=request['depth']<max_depth):
        raise ValueError('Provisional depth is outside the allocated search')
    budget=request.get('budget_s',82)
    if type(budget) not in (int,float) or not 0<budget<=82:raise ValueError('Provisional native budget must be positive and at most 82 seconds')
    deadline=time.monotonic()+budget
    board=p.LoadBoard(str(path));validate(board,dict(request,board=str(path)))
    before=snapshot(board);protected=set(request.get('protected_ids',()))
    if request.get('operation')=='final_frame':
        original=p.LoadBoard(str(reference));validate(original,dict(request,board=str(reference)))
        require_provisional_frame(snapshot(original),before,protected)
        if pad_gaps(board):raise ValueError('A final lookahead frame still has independent pad gaps')
        if not partitions_preserved(pad_partitions(original,deadline),pad_partitions(board,deadline)):
            raise ValueError('Lookahead lost original connected pad partitions')
        require_via_land_clearance(board,deadline=deadline)
        return {'provisional_only':True,'accepted_final':False,'original_frame_preserved':True,'original_pad_partitions_preserved':True}
    if request.get('operation')!='provisional':raise ValueError('Unknown provisional worker operation')
    protected|={uid for uid,t in before['tracks'].items() if t['locked'] or t['net'] in set(request.get('protect_nets',()))}
    # Enforce missing protected objects before invoking any mutating search.
    require_provisional_frame(before,before,protected)
    old=pad_gaps(board)
    cfg=json.loads((path.parent/'.smilepcb-keyboard.json').read_text())
    strategy='plane_negotiate' if elastic and set(old)&set(cfg['plane_nets']) else 'signal_negotiate'
    native=repair_candidate(board,strategy,protected=protected,plane_nets=set(cfg['plane_nets']),request={},deadline=deadline)
    missing=pad_gaps(board)
    require_provisional_frame(before,snapshot(board),protected)
    decision=provisional_step(old,missing,seen={tuple(tuple(pair) for pair in signature) for signature in request.get('seen',())},depth=request['depth'],max_depth=max_depth,gap_ceiling=ceiling)
    if decision['allowed']:
        require_via_land_clearance(board,deadline=deadline)
        if time.monotonic()>=deadline:raise TimeoutError('Provisional save deadline exhausted')
        if not p.SaveBoard(str(path),board):raise ValueError('Cannot save disposable provisional board')
    return {'provisional_only':True,'accepted_final':False,'saved_trial':decision['allowed'],
            'before':old,'after':missing,'protect_healed':sorted(decision['protect_healed']),'native_repair':native,'strategy':strategy}


if __name__=='__main__':print(json.dumps(run(json.load(sys.stdin))))
