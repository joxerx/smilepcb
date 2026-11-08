"""Bounded conditional complete-bank control; no native geometry or PCB claims.

The adapter alone certifies complete plans and physical failed interfaces.
Search owns prefix provenance, rollback, attempt limits and the leaf-only gate.
Bank/net labels are opaque; order ties use actual source-pad identities.
"""
from copy import deepcopy
from itertools import combinations
import math
from time import monotonic


def _interfaces(value):
    if not isinstance(value, (list, tuple)):
        raise ValueError('Failed interfaces must be physical pad-ID sets')
    groups = set()
    for group in value:
        if (not isinstance(group, (list, tuple)) or not group or
                any(not isinstance(uid, str) or not uid for uid in group) or
                len(set(group)) != len(group)):
            raise ValueError('Invalid failed physical interface')
        groups.add(tuple(sorted(group)))
    used = set()
    for group in groups:
        if used.intersection(group):
            raise ValueError('Distinct failed physical interfaces overlap')
        used.update(group)
    return sorted(groups)


def failed_first_order(source_interfaces, failed_interfaces):
    """Return a stable full-interface priority order, or None if unsupported/no-op.

    The native adapter supplies the complete physical inventory and certifies
    unresolved interfaces. Partial aliases, overlap and unknown identities do
    not become a routing-order hint. This function changes no caller objects.
    """
    try:
        _interfaces(source_interfaces)
        failed = _interfaces(failed_interfaces)
    except (TypeError, ValueError):
        return None
    keys = [tuple(sorted(group)) for group in source_interfaces]
    if (not keys or len(keys) != len(set(keys)) or not failed or
            not set(failed) <= set(keys)):
        return None
    priority = set(failed)
    ordered = [key for key in keys if key in priority] + [key for key in keys if key not in priority]
    return ordered if ordered != keys else None


def conditional_search(order, attempt, *, max_attempts=16, deadline, leaf_validator, retry_unresolved_first=False):
    """Depth-first original/regenerated choices with an optional same-head retry.

    Callback inputs are isolated copies, and mutating them is a contract error.
    Only returned ``failed_interfaces`` from this parent's original attempt
    become regenerated-head feedback. When explicitly enabled, a completed
    final native missing graph can first schedule one original-head feeder-order
    retry. Its failures never replace the original regeneration feedback. A raised TimeoutError carries no such
    certificate. Other callback errors propagate instead of becoming geometry
    failures. The deadline bounds attempts AND leaf validation.

    Frontier rows represent unexpanded subtrees, not promises of an available
    domain. Accepted plan objects are retained in attempt records so each
    prefix_attempts path can be reconstructed without ambiguous plan IDs.
    """
    order = tuple(order)
    if not order or any(not isinstance(b, str) or not b for b in order) or len(set(order)) != len(order):
        raise ValueError('Expected distinct nonempty physical bank identifiers')
    if type(max_attempts) is not int or not 1 <= max_attempts <= 16:
        raise ValueError('Conditional search allows one to sixteen attempts')
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError('Expected a finite monotonic deadline')
    if type(retry_unresolved_first) is not bool:
        raise ValueError('Same-head priority retry must be an explicit boolean')
    records = []
    retry_decisions = []
    leaves = []
    leaf_count = 0
    def new_frame(depth, plans, ids):
        return {'depth': depth, 'plans': plans, 'ids': ids, 'next': 0, 'feedback': [],
                'feedback_origin_attempt': None,
                'options': ['original', 'original_priority', 'regenerated'] if retry_unresolved_first else ['original', 'regenerated']}
    stack = [new_frame(0, [], [])]

    def frontier():
        rows = []
        for frame in reversed(stack):
            depth = frame['depth']
            if depth == len(order):
                rows.append({'depth': depth, 'bank': None, 'option': 'validate_leaf',
                    'prefix_attempts': list(frame['ids']), 'feedback': [],
                    'feedback_status': 'known', 'remaining_banks': []})
                continue
            for choice in range(frame['next'], len(frame['options'])):
                rows.append({'depth': depth, 'bank': order[depth],
                    'option': frame['options'][choice],
                    'prefix_attempts': list(frame['ids']),
                    'feedback': deepcopy(frame['feedback']) if choice else [],
                    'feedback_status': 'pending_original' if choice and frame['next'] == 0 else 'known',
                    'feedback_origin_attempt': frame['feedback_origin_attempt'] if choice else None,
                    'remaining_banks': list(order[depth + 1:])})
        return rows

    def finish(reason, *, selection=(), ids=(), leaf_result=None):
        return {'accepted': reason == 'accepted', 'reason': reason,
            'selection': deepcopy(list(selection)), 'selection_attempts': list(ids),
            'attempt_records': records, 'leaf_rejections': leaves, 'retry_decisions': retry_decisions,
            'leaf_validations': leaf_count, 'leaf_result': deepcopy(leaf_result),
            'frontier': [] if reason in ('accepted', 'finite_search_exhausted') else frontier(),
            'physical_impossibility_proven': False}

    while stack:
        frame = stack[-1]
        depth = frame['depth']
        if monotonic() >= deadline:
            return finish('deadline')
        if depth < len(order) and frame['next'] == len(frame['options']):
            stack.pop()
            continue
        if depth == len(order):
            inputs = deepcopy(frame['plans'])
            unchanged = deepcopy(inputs)
            tick = monotonic()
            leaf_count += 1
            try:
                outcome = leaf_validator(inputs)
            except TimeoutError as error:
                outcome = {'accepted': False, 'reason': 'leaf_timeout', 'detail': str(error)}
            if inputs != unchanged:
                raise ValueError('Leaf validator mutated selected prefix plans')
            if not isinstance(outcome, dict) or type(outcome.get('accepted')) is not bool:
                raise ValueError('Leaf validator must explicitly return boolean accepted')
            outcome = deepcopy(outcome)
            elapsed = monotonic() - tick
            if monotonic() >= deadline:
                leaves.append({'prefix_attempts': list(frame['ids']), 'elapsed_s': elapsed,
                    'result': {'accepted': False, 'reason': 'global_deadline_after_leaf',
                               'reported_result': outcome}})
                # Validation did run; its expired verdict is not a pending leaf.
                stack.pop()
                return finish('deadline')
            if outcome['accepted']:
                return finish('accepted', selection=frame['plans'], ids=frame['ids'], leaf_result=outcome)
            leaves.append({'prefix_attempts': list(frame['ids']), 'elapsed_s': elapsed, 'result': outcome})
            stack.pop()
            continue
        if len(records) >= max_attempts:
            return finish('attempt_limit')
        choice = frame['next']
        frame['next'] += 1
        bank = order[depth]
        option = frame['options'][choice]
        feedback = deepcopy(frame['feedback']) if choice else []
        inputs = deepcopy(frame['plans'])
        unchanged = deepcopy((inputs, feedback))
        tick = monotonic()
        try:
            outcome = attempt(bank, option, inputs, feedback, deadline)
        except TimeoutError as error:
            outcome = {'accepted': False, 'reason': 'attempt_timeout', 'detail': str(error)}
        if (inputs, feedback) != unchanged:
            raise ValueError('Attempt callback mutated retained prefix or feedback')
        if not isinstance(outcome, dict) or type(outcome.get('accepted')) is not bool:
            raise ValueError('Attempt must explicitly return boolean accepted')
        outcome = deepcopy(outcome)
        failures = _interfaces(outcome.get('failed_interfaces', []))
        plan = outcome.pop('plan', None)
        if outcome['accepted']:
            if not isinstance(plan, dict) or plan.get('bank') != bank or failures:
                raise ValueError('Accepted complete-bank plan has wrong ownership or failed interfaces')
        elif plan is not None:
            raise ValueError('Rejected attempt must not return an accepted plan')
        if option == 'original':
            frame['feedback'] = failures
            frame['feedback_origin_attempt'] = len(records) + 1
            if retry_unresolved_first:
                certified = not outcome['accepted'] and outcome.get('feedback_basis') == 'completed_final_native_graph_after_scheduled_routing'
                priority = failed_first_order(outcome.get('source_interfaces'), failures) if certified else None
                retry_decisions.append({'bank': bank, 'prefix_attempts': list(frame['ids']),
                    'original_attempt': len(records) + 1, 'scheduled': priority is not None,
                    'feeder_interfaces': deepcopy(priority),
                    'reason': 'certified_final_graph_unresolved_interfaces' if priority is not None else
                              'unsupported_or_noop_physical_order' if certified else 'original_not_certified_unresolved'})
                if priority is None:
                    frame['options'].remove('original_priority')
        index = len(records) + 1
        record = {'index': index, 'depth': depth, 'bank': bank, 'option': option,
            'prefix_attempts': list(frame['ids']), 'feedback': deepcopy(unchanged[1]),
            'accepted': outcome['accepted'], 'result': outcome, 'elapsed_s': monotonic() - tick,
            'feedback_origin_attempt': frame['feedback_origin_attempt'] if option != 'original' else None}
        if plan is not None:
            record['plan'] = deepcopy(plan)
        records.append(record)
        if outcome['accepted']:
            stack.append(new_frame(depth + 1, frame['plans'] + [deepcopy(plan)], frame['ids'] + [index]))
    return finish('finite_search_exhausted')


def derive_cold_bank_order(banks, side_sets):
    """Order current physical banks by adjacent interface load, without probes.

    ``banks`` maps opaque bank IDs to actual source ports with ``pad_ids``.
    ``side_sets`` gives each alias-unioned bank's MCU-local cardinal sides as
    ``(axis, sign)`` pairs. Native pad geometry must supply those sides; labels
    and logical net names never imply direction. Two sides on different axes
    are adjacent. Source aliases count as one interface, not multiple lanes.

    The adjacent pair with greatest combined interface count goes first,
    larger bank first. Remaining banks follow by interface count. Sorted
    physical source-pad identities break exact ties. This is an ordering
    heuristic, not a native interference or capacity certificate.
    """
    if (not isinstance(banks, dict) or not banks or not isinstance(side_sets, dict)
            or set(banks) != set(side_sets)
            or any(not isinstance(bank, str) or not bank for bank in banks)):
        raise ValueError('Expected exact physical bank and side inventories')
    counts = {}
    keys = {}
    sides = {}
    owned_pads = set()
    owned_sides = set()
    for bank, ports in banks.items():
        if not isinstance(ports, (list, tuple)) or not ports:
            raise ValueError('Physical bank has no source interfaces')
        physical = []
        for port in ports:
            pads = port.get('pad_ids') if isinstance(port, dict) else None
            if (not isinstance(pads, (list, tuple)) or not pads
                    or any(not isinstance(uid, str) or not uid for uid in pads)
                    or len(set(pads)) != len(pads) or owned_pads.intersection(pads)):
                raise ValueError('Physical source-pad ownership is missing or overlapping')
            owned_pads.update(pads)
            physical.extend(pads)
        raw_sides = side_sets[bank]
        if not isinstance(raw_sides, (list, tuple, set, frozenset)) or not raw_sides:
            raise ValueError('Physical bank needs actual MCU-local sides')
        current = set()
        for side in raw_sides:
            if (not isinstance(side, (list, tuple)) or len(side) != 2
                    or any(type(value) is not int for value in side)
                    or side[0] not in (0, 1) or side[1] not in (-1, 1)):
                raise ValueError('Expected MCU-local cardinal axis/sign pairs')
            side = tuple(side)
            if side in current or side in owned_sides:
                raise ValueError('Physical MCU side belongs to more than one bank')
            current.add(side)
        owned_sides.update(current)
        sides[bank] = current
        counts[bank] = len(ports)
        keys[bank] = tuple(sorted(physical))
    def rank(bank):
        return (-counts[bank], keys[bank])
    adjacent = [(a, b) for a, b in combinations(banks, 2)
                if any(x[0] != y[0] for x in sides[a] for y in sides[b])]
    order = []
    if adjacent:
        pair = min(adjacent, key=lambda pair: (
            -sum(counts[b] for b in pair), tuple(sorted(keys[b] for b in pair))))
        order.extend(sorted(pair, key=rank))
    order.extend(sorted((bank for bank in banks if bank not in order), key=rank))
    return order
