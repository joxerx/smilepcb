"""One bounded finalized-board group trial, without a full routing restart.

Run with Native KiCad Python: ``python -m
smilepcb.keyboard_routing.finalized_research request.json report.json``.
The request identifies an immutable board SHA, inventory scope (nets/MCU/bounds)
and optional previously discovered axes. Every axis and collision is re-proved
on the current board. This entry point never saves or qualifies a board; its
operation receipt is consumed by the transactional publisher and ERC/DRC step.
"""
import hashlib
import json
from pathlib import Path
import sys
import time


def quality_decision(quality):
    """Same coverage and complete-route V2 thresholds as the research gate."""
    if quality is None:
        return 'no-candidate'
    coverage_ok = all(quality['after_coverage'][role]+1 >= value
                      for role, value in quality['before_coverage'].items())
    gains = [quality['after_objective'][role]['score_nm']-value['score_nm']
             for role, value in quality['before_objective'].items()]
    admitted = coverage_ok and gains and min(gains) >= -1 and max(gains) >= 500000
    return 'quality-admitted-awaiting-saved-ERC-DRC' if admitted else 'feasibility-only-quality-rejected'


def run(request):
    from smilepcb.native_worker import p, snapshot
    from smilepcb.keyboard_phases import ordered_nets
    from smilepcb.routing_quality import copper_metrics
    from .bundle_turns import bundle_objective
    from .role_quality import role_coverage
    from .obstacle_inventory import inventory_windows
    from .obstacle_bundles import project_operations
    from .source_prefixes import source_prefix_variant
    from .source_attachment_witnesses import source_attachment_witnesses
    from .finalized_bundle import plan_finalized_group

    source_strategy = request.get('source_strategy', 'sequential')
    if source_strategy not in ('sequential', 'joint', 'staged-joint'):
        raise ValueError('Unknown source strategy')
    if source_strategy == 'staged-joint' and request.get('source_mode', 'owned-prefix') != 'direct':
        raise ValueError('Staged joint requires source_mode=direct')
    path = Path(request['board']).resolve()
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if digest != request['source_sha256']:
        raise ValueError('Research input changed from its recorded source hash')
    budget = request.get('budget_s', 32)
    if type(budget) not in (int, float) or not 1 <= budget <= 60:
        raise ValueError('One local trial requires a 1..60 second budget')
    started = time.monotonic(); deadline = started + budget
    board = p.LoadBoard(str(path)); owners = (board.GetProject(), board.GetDesignSettings())
    before = snapshot(board)
    protected = {uid for uid, row in before['tracks'].items()
                 if row['locked'] or row['net'] in ('USB_DP', 'USB_DM')}
    mcu_ref = request.get('mcu_ref', 'U_MCU')
    mcu = next(fp for fp in board.GetFootprints() if fp.GetReference() == mcu_ref)
    position = mcu.GetPosition()
    bounds = request.get('bounds_nm', [position.x-24000000, position.y-18000000,
                                       position.x+24000000, position.y+18000000])
    zones = [z.m_Uuid.AsString() for z in board.Zones() if not z.GetIsRuleArea()]
    stamp = time.monotonic()
    rows, inventory = inventory_windows(board, set(request['nets']), mcu_ref=mcu_ref,
        bounds=bounds, protected_ids=protected, deadline=deadline, deferred_zone_ids=zones)
    by_name = {row['net']: row for row in rows}
    if set(request['nets']) != set(by_name):
        raise ValueError('Requested group has missing or ambiguous current inventory')
    group = [by_name[name] for name in request['nets']]
    stages = dict(inventory_s=time.monotonic()-stamp)
    axes = request.get('candidate_axes')
    source_mode = request.get('source_mode', 'owned-prefix')
    witnesses = dict(witness_ids=[], carriers=[])
    if axes:
        stamp = time.monotonic()
        variants = group if source_mode == 'direct' else [source_prefix_variant(board, row, protected) or row for row in group]
        witnesses = source_attachment_witnesses(board, variants, axes[0],
            bounds=bounds, protected_ids=protected, deadline=deadline, deferred_zone_ids=zones)
        stages['collision_preflight_s'] = time.monotonic()-stamp
    stamp = time.monotonic()
    plan = plan_finalized_group(board, group, bounds=bounds,
        preferred_layer=request.get('preferred_layer', p.F_Cu), protected_ids=protected,
        deadline=deadline, candidate_axes=axes, plane_witness_ids=witnesses['witness_ids'],
        source_mode=source_mode, source_strategy=source_strategy)
    stages['plan_s'] = time.monotonic()-stamp
    if snapshot(board) != before or path.read_bytes() != original:
        raise ValueError('Read-only research trial changed its input')
    quality = None
    if plan.get('planned'):
        cfg = json.loads((path.parent/'.smilepcb-keyboard.json').read_text())
        roles = {name: set(nets) for name, nets in ordered_nets(cfg) if name in ('columns', 'rows')}
        proposed = project_operations(before['tracks'], plan['removed_ids'], plan['operations'])
        quality = dict(before_metrics=copper_metrics(before['tracks']), after_metrics=copper_metrics(proposed),
            before_coverage=role_coverage(before['tracks'], roles, bounds),
            after_coverage=role_coverage(proposed, roles, bounds),
            before_objective=bundle_objective(before['tracks'], roles, bounds),
            after_objective=bundle_objective(proposed, roles, bounds))
    return dict(source_sha256=digest, source_exact=True, inventory=inventory,
        collision_preflight=witnesses, bounds_nm=bounds, stages=stages, quality=quality,
        selection_status=quality_decision(quality), qualified=False,
        plan={k:v for k,v in plan.items() if k != 'source_snapshot'},
        elapsed_s=time.monotonic()-started, scope='One local group; saved ERC/DRC required')


if __name__ == '__main__':
    result = run(json.loads(Path(sys.argv[1]).read_text()))
    Path(sys.argv[2]).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(dict(planned=result['plan'].get('planned'), stages=result['stages'],
                         elapsed_s=result['elapsed_s']), indent=2))
