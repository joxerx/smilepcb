"""Transactional replacement of complete, current-attempt matrix feeders.

Existing source, collector, field and prefix copper is immutable within a journal.
The caller owns the absolute deadline and final complete-bank acceptance. This
module neither allocates a bank budget nor publishes a conditional bank plan.
"""
from copy import deepcopy
from dataclasses import dataclass, field
import math
import hashlib
import json
import time
from types import SimpleNamespace
from typing import Optional


@dataclass
class FeederJournal:
    bank_nets: frozenset
    frame: dict
    ports: dict
    physical: str
    context: tuple
    owners: dict = field(default_factory=dict)
    witnesses: dict = field(default_factory=dict)
    current_physical: Optional[str] = None
    repair_reserved: frozenset = field(default_factory=frozenset)
    baseline_seal: Optional[str] = None


@dataclass(frozen=True)
class RepairResult:
    improved: bool
    before_missing: Optional[dict]
    after_missing: Optional[dict]
    retired_ids: tuple = ()
    added_shapes: dict = field(default_factory=dict)
    attempts: tuple = ()
    reason: Optional[str] = None


def feeder_delta(before, after, indexed_added, net, protected):
    """Certify one indexed append without granting authority by net name."""
    old, live = before['tracks'], after['tracks']
    indexed_added = tuple(indexed_added)
    added = live.keys() - old.keys()
    if (not set(protected) <= old.keys() or before['footprints'] != after['footprints']
            or any(live.get(uid) != shape for uid, shape in old.items())
            or len(indexed_added) != len(set(indexed_added)) or set(indexed_added) != added):
        raise ValueError('Feeder route changed its parent or indexed delta')
    if any(live[uid]['net'] != net or live[uid]['locked']
           or live[uid]['class'] not in ('PCB_TRACK', 'PCB_VIA') for uid in added):
        raise ValueError('Feeder route emitted foreign or unsupported copper')
    return deepcopy({uid: live[uid] for uid in added})


def owner_cohort(journal, target, closed_nets):
    """One exact yielded witness licenses the complete eligible owner only."""
    if target not in journal.bank_nets:
        raise ValueError('Repair target is outside this bank')
    owners = set()
    for uid, witness in journal.witnesses.get(target, {}).items():
        shape = journal.owners.get(uid)
        if (shape is None or uid in journal.frame['tracks'] or shape != witness['shape']
                or shape['net'] != witness['net'] or shape['net'] == target
                or shape['net'] not in closed_nets or shape['net'] not in journal.bank_nets):
            raise ValueError('Collision witness is not a closed current feeder owner')
        owners.add(shape['net'])
    soft = sorted(uid for uid, shape in journal.owners.items() if shape['net'] in owners)
    if any(uid in journal.frame['tracks'] or journal.owners[uid]['locked']
           or journal.owners[uid]['class'] not in ('PCB_TRACK', 'PCB_VIA') for uid in soft):
        raise ValueError('Complete feeder owner contains protected or unsupported copper')
    return sorted(owners), soft


def _snapshot(board):
    from smilepcb.native_worker import snapshot
    return snapshot(board)


def _inventory():
    from .interface_launches import current_launch_inventory
    inventory = current_launch_inventory()
    if inventory is None:
        raise ValueError('Owned feeder routing requires a current launch scope')
    return inventory


def _physical(board, excluded=()):
    from .conditional_geometry import FrozenProofRouter
    return FrozenProofRouter._physical_state(SimpleNamespace(board=board), excluded_ids=excluded)


def _context(board):
    from .conditional_geometry import FrozenProofRouter
    from .generation import current_generation_profile
    from smilepcb.routing_quality import current_quality
    project = board.GetProject()
    return (FrozenProofRouter._rule_state(SimpleNamespace(board=board)),
            board.GetDesignSettings().m_NetSettings.GetDefaultNetclass().GetClearance(),
            str(board.GetFileName()), int(project) if project is not None else None,
            current_generation_profile(), current_quality())


def _guard(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError('Owned feeder repair deadline exhausted')


def _role_seal(journal):
    roles = {'frame': journal.frame, 'bank_nets': sorted(journal.bank_nets),
             'ports': [(list(key), port) for key, port in sorted(journal.ports.items())],
             'repair_reserved': sorted(journal.repair_reserved),
             'physical': journal.physical, 'context': repr(journal.context)}
    return hashlib.sha256(json.dumps(roles, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _validate(board, journal, frame=None):
    if _role_seal(journal) != journal.baseline_seal:
        raise ValueError('Postcollector journal roles were changed')
    frame = _snapshot(board) if frame is None else frame
    if (frame['footprints'] != journal.frame['footprints']
            or frame['tracks'] != journal.frame['tracks'] | journal.owners
            or journal.frame['tracks'].keys() & journal.owners.keys()
            or any(q['net'] not in journal.bank_nets or q['locked'] for q in journal.owners.values())
            or _inventory().ports != journal.ports or _inventory().repair_reserved != journal.repair_reserved
            or _context(board) != journal.context
            or _physical(board) != journal.current_physical
            or _physical(board, journal.owners) != journal.physical):
        raise ValueError('Owned feeder journal no longer matches the current native parent')
    return frame


def begin_feeders(board, bank_nets):
    """Start after collectors; every existing native copper object is protected."""
    nets = frozenset(bank_nets)
    ports = deepcopy(_inventory().ports)
    frame = _snapshot(board)
    if not nets or not nets <= {port['net'] for port in ports.values()}:
        raise ValueError('Feeder journal lacks current bank source interfaces')
    if not {uid for port in ports.values() for uid in port['added_ids']} <= frame['tracks'].keys():
        raise ValueError('Current source ownership is absent from the postcollector frame')
    physical = _physical(board)
    journal = FeederJournal(nets, deepcopy(frame), ports, physical, _context(board),
                            current_physical=physical, repair_reserved=frozenset(_inventory().repair_reserved))
    journal.baseline_seal = _role_seal(journal)
    return journal


def record_feeder_route(board, journal, before, indexed_added, net):
    """Update authority only after verifying native append and retained properties."""
    if _role_seal(journal) != journal.baseline_seal:
        raise ValueError('Postcollector journal roles were changed')
    if net not in journal.bank_nets:
        raise ValueError('Cannot journal another bank or a fixed net')
    if before['footprints'] != journal.frame['footprints'] or before['tracks'] != journal.frame['tracks'] | journal.owners:
        raise ValueError('Feeder append does not start at the journal parent')
    after = _snapshot(board)
    added = feeder_delta(before, after, indexed_added, net, journal.frame['tracks'])
    if (_inventory().ports != journal.ports or _inventory().repair_reserved != journal.repair_reserved
            or _context(board) != journal.context
            or _physical(board, added) != journal.current_physical):
        raise ValueError('Feeder append changed retained native properties or current ports')
    physical = _physical(board)
    journal.owners.update(added)
    journal.current_physical = physical
    return added


def feeder_router(board, journal, *, deadline):
    """Fresh instance-local observer; begin_route binds a finite witness inventory."""
    from .matrix import KeyboardRouter

    class ObservedRouter(KeyboardRouter):
        def begin_route(self, net):
            _guard(self.deadline)
            _validate(board, journal)
            ports = [port for port in journal.ports.values() if port['net'] == net]
            if len(ports) != 1:
                raise ValueError('Owned repair needs one physical source interface per net')
            self.observed_net = net
            self.observed_tip = tuple(ports[0]['exit_nm'])
            self.observed_owners = deepcopy(journal.owners)
            self.query_keys = set()
            self.witnesses = {}
            self.query_omissions = 0

        def collisions(self, shape, layer, net, via=False):
            observe = False
            if getattr(self, 'observed_net', None) == net:
                x, y = self.observed_tip
                box = shape.BBox()
                if (box.GetLeft() <= x + 1000000 and box.GetRight() >= x - 1000000
                        and box.GetTop() <= y + 1000000 and box.GetBottom() >= y - 1000000):
                    key = (layer, via, shape.Format(False))
                    if key in self.query_keys or len(self.query_keys) < 256:
                        self.query_keys.add(key)
                        observe = True
                    else:
                        self.query_omissions += 1
            for uid in super().collisions(shape, layer, net, via):
                if observe and uid in self.observed_owners:
                    owned = self.observed_owners[uid]
                    witness = {'net': owned['net'], 'shape': deepcopy(owned)}
                    self.witnesses[uid] = witness
                    journal.witnesses.setdefault(net, {})[uid] = deepcopy(witness)
                yield uid

    return ObservedRouter(board, deadline=deadline, compact=True,
                          track_width_nm=board.GetDesignSettings().m_TrackMinWidth or 150000)


def _assess(board, journal, deadline):
    from smilepcb.native_worker import p
    from .native_graph import copper_graph
    from .feeder_repair import _pad_state
    _guard(deadline)
    frame = _validate(board, journal)
    expected = {pad['uuid']: pad['net'] for part in journal.frame['footprints'].values()
                for pad in part['pads'] if pad['number'] and pad['net']}
    coverage = set(expected.values()) | journal.bank_nets | {port['net'] for port in journal.ports.values()}
    graph = copper_graph(board, coverage, deadline)
    _inventory().refresh(graph)
    nodes = graph['nodes']
    ids = {node['item'].m_Uuid.AsString(): i for i, node in enumerate(nodes) if not node['zone']}
    actual, partitions, pad_roots = {}, {}, set()
    for uid, index in ids.items():
        item = nodes[index]['item']
        if isinstance(item, p.PAD) and item.GetNumber() and item.GetNetname():
            actual[uid] = item.GetNetname()
            root = graph['find'](index)
            pad_roots.add(root)
            partitions.setdefault(item.GetNetname(), {}).setdefault(root, set()).add(uid)
    if actual != expected:
        raise ValueError('Repair changed the physical numbered-pad inventory')
    for port in journal.ports.values():
        members = set(port['pad_ids']) | set(port['added_ids']) | {port.get('terminal_id') or port.get('via_id')}
        members.discard(None)
        if (not members <= ids.keys() or any(nodes[ids[uid]]['net'] != port['net'] for uid in members)
                or len({graph['find'](ids[uid]) for uid in members}) != 1):
            raise ValueError('Repair lost a current source or collector contact')
    if any(uid not in ids or graph['find'](ids[uid]) not in pad_roots for uid in journal.owners):
        raise ValueError('A generated feeder survivor detached from its physical terminals')
    state = _pad_state(graph, journal.bank_nets)
    if any(value < 1 for value in state['groups'].values()):
        raise ValueError('Repair bank has a missing physical terminal inventory')
    _guard(deadline)
    return {'missing': state['missing'], 'graph': graph, 'coverage': coverage,
            'partitions': {net: {frozenset(group) for group in groups.values()}
                           for net, groups in partitions.items()}, 'frame': frame,
            'attached': {uid for uid, index in ids.items() if graph['find'](index) in pad_roots}}


def _progress(before, after, bank_nets, retired=()):
    if not set(after['missing']) <= set(before['missing']) or sum(after['missing'].values()) >= sum(before['missing'].values()):
        return False
    if not before['attached'] - set(retired) <= after['attached']:
        return False
    for net, groups in before['partitions'].items():
        current = after['partitions'].get(net, set())
        if net not in bank_nets and current != groups:
            return False
        if any(not any(group <= other for other in current) for group in groups):
            return False
    return True


def _prove_feeders(board, journal, state, deadline):
    from .conditional_geometry import prove_copper
    from .safety import require_via_land_clearance
    if prove_copper(board, set(journal.owners), state['graph'], state['coverage'], deadline) != len(journal.owners):
        raise ValueError('Saved feeder proof omitted a requested survivor')
    require_via_land_clearance(board, deadline=deadline)
    _validate(board, journal)
    _guard(deadline)


def _checkpoint(board, journal):
    from .conditional_geometry import copy_native_tracks
    inventory = _inventory()
    return {'frame': _snapshot(board), 'physical': _physical(board), 'context': _context(board),
            'copies': copy_native_tracks(board), 'journal': deepcopy(journal.__dict__),
            'launches': (deepcopy(inventory.ports), set(inventory.resolved), set(inventory.repair_reserved))}


def _restore(board, journal, saved):
    from smilepcb.native_worker import p
    def raw_equal(item, template):
        if item.GetLocalClearance() != template.GetLocalClearance():
            return False
        if isinstance(item, p.PCB_VIA) and item.GetEffectiveHoleShape().Format(False) != template.GetEffectiveHoleShape().Format(False):
            return False
        return all(item.IsOnLayer(face) == template.IsOnLayer(face)
                   and (not item.IsOnLayer(face) or item.GetEffectiveShape(face).Format(False)
                        == template.GetEffectiveShape(face).Format(False)) for face in (p.F_Cu, p.B_Cu))
    live = _snapshot(board)['tracks']
    for item in list(board.GetTracks()):
        uid = item.m_Uuid.AsString()
        if (uid not in saved['frame']['tracks'] or live[uid] != saved['frame']['tracks'][uid]
                or not raw_equal(item, saved['copies'][uid])):
            board.Delete(item)
    present = {item.m_Uuid.AsString() for item in board.GetTracks()}
    for uid, template in saved['copies'].items():
        if uid not in present:
            clone = template.Duplicate()
            clone.SetUuid(p.KIID(uid))
            board.Add(clone)
    board.BuildConnectivity()
    inventory = _inventory()
    inventory.ports, inventory.resolved, inventory.repair_reserved = deepcopy(saved['launches'])
    journal.__dict__.clear()
    journal.__dict__.update(deepcopy(saved['journal']))
    if (_snapshot(board) != saved['frame'] or _physical(board) != saved['physical']
            or _context(board) != saved['context']):
        raise RuntimeError('Owned feeder repair could not restore its exact native parent')


def _orders(target, incumbents, failed=()):
    failed = set(failed)
    orders = []
    for values in (list(incumbents), list(reversed(incumbents))):
        order = [target, *values]
        if order not in orders:
            orders.append(order)
    if failed:
        for values in (list(incumbents), list(reversed(incumbents))):
            order = [target, *[net for net in values if net in failed], *[net for net in values if net not in failed]]
            if order not in orders:
                orders.append(order)
    return orders[:4]


def _priority_orders(order, events, missing, baseline):
    """New candidate copper can change precedence, never deletion authority."""
    positions = {net: i for i, net in enumerate(order)}
    emitted = {uid: (event['net'], shape) for event in events for uid, shape in event['added'].items()}
    proposals = []
    for event in events:
        failed = event['net']
        if failed not in missing:
            continue
        owners = set()
        for uid, witness in event['witnesses'].items():
            if uid in baseline or uid not in emitted:
                continue
            owner, shape = emitted[uid]
            if (owner in positions and shape == witness['shape'] and shape['net'] == owner == witness['net']
                    and positions[owner] < positions[failed]):
                owners.add(owner)
        if owners:
            first = min(owners, key=positions.__getitem__)
            proposal = [net for net in order if net != failed]
            proposal.insert(proposal.index(first), failed)
            if proposal not in proposals:
                proposals.append(proposal)
        if len(proposals) == 2:
            break
    return proposals


def _route_candidate(board, journal, order, deadline):
    from .native_graph import route_components
    events = []
    router = None
    for net in order:
        if time.monotonic() >= deadline - 3:
            break
        stop = min(deadline - 3, time.monotonic() + 5)
        if router is None:
            router = feeder_router(board, journal, deadline=stop)
        else:
            router.deadline = stop
        for name in ('last_search', 'last_plan', 'graph_diagnostic', 'attempt_counts'):
            if hasattr(router, name):
                delattr(router, name)
        router.grid_step = 125000
        router.search_budget_s = router.connection_budget_s = 4
        router.search_expansion_limit = 200000
        router.heuristic_weight = 3
        router.via_cost_nm = 1000000
        router.layer_change_heuristic_nm = 0
        router.routing_penalty = None
        if router.expired():
            break
        router.begin_route(net)
        if router.expired():
            break
        before, mark = _snapshot(board), len(router.added)
        count = route_components(board, router, {net}, finer_grid_steps=())
        added = record_feeder_route(board, journal, before, router.added[mark:], net)
        events.append({'net': net, 'connections': count, 'added': added,
                       'witnesses': deepcopy(router.witnesses), 'query_omissions': router.query_omissions})
    return events


def repair_owned_feeders(board, journal, target, *, deadline):
    """Attempt one strict bank improvement; the caller must discard old routers.

    The returned result is not a bank certificate. Rejection restores tracks,
    raw native properties, current launches and journal authority exactly.
    Unexpected exceptions propagate after restoration; restoration failure is fatal.
    """
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError('Owned repair requires a finite absolute caller deadline')
    if time.monotonic() >= deadline:
        return RepairResult(False, None, None, reason='deadline_before_assessment')
    _validate(board, journal)
    if time.monotonic() >= deadline:
        return RepairResult(False, None, None, reason='deadline_after_journal_validation')
    saved = _checkpoint(board, journal)
    before = None
    attempts = []
    improved = False
    try:
        before = _assess(board, journal, deadline)
        if target not in before['missing']:
            return RepairResult(False, before['missing'], before['missing'], reason='target_already_closed')
        closed = journal.bank_nets - before['missing'].keys()
        try:
            owners, _ = owner_cohort(journal, target, closed)
        except ValueError as error:
            return RepairResult(False, deepcopy(before['missing']), deepcopy(before['missing']), reason='owner_scope_rejected: ' + str(error))
        if not owners:
            return RepairResult(False, before['missing'], before['missing'], reason='no_owned_collision_witness')
        physical_order = [port['net'] for port in journal.ports.values() if port['net'] in journal.bank_nets]
        if len(physical_order) != len(set(physical_order)):
            raise ValueError('Unsupported duplicate physical bank interface')
        cohort = set(owners)
        for expansion in range(3):
            soft = sorted(uid for uid, shape in journal.owners.items() if shape['net'] in cohort)
            incumbents = [net for net in physical_order if net in cohort]
            orders = _orders(target, incumbents)
            physical = deepcopy(orders)
            ordinary = deepcopy(orders)
            priority = []
            failed = set()
            revealed = set()
            for order in orders:
                if order in priority:
                    priority.remove(order)
                _guard(deadline)
                stop = min(deadline, time.monotonic() + max(20, 5 * len(order) + 10))
                working = deepcopy(journal)
                events = []
                after = None
                row = {'order': list(order), 'expansion': expansion, 'improved': False}
                attempts.append(row)
                try:
                    remaining_physical = _physical(board, soft)
                    for item in list(board.GetTracks()):
                        if item.m_Uuid.AsString() in soft:
                            board.Delete(item)
                    board.BuildConnectivity()
                    working.owners = {uid: shape for uid, shape in working.owners.items() if uid not in soft}
                    working.witnesses = {}
                    working.current_physical = remaining_physical
                    _assess(board, working, stop)
                    events = _route_candidate(board, working, order, stop)
                    after = _assess(board, working, stop)
                    row.update(missing=deepcopy(after['missing']), routed=[event['net'] for event in events],
                               query_omissions=sum(event['query_omissions'] for event in events))
                    if not _progress(before, after, journal.bank_nets, soft):
                        raise ValueError('no_strict_native_improvement')
                    _prove_feeders(board, working, after, stop)
                    if _inventory().repair_reserved != saved['launches'][2]:
                        raise ValueError('Repair changed held launch reservations')
                    _guard(stop)
                    journal.__dict__.clear()
                    journal.__dict__.update(deepcopy(working.__dict__))
                    journal.witnesses = {}
                    added = {uid: shape for uid, shape in journal.owners.items() if uid not in saved['journal']['owners']}
                    _guard(stop)
                    row['improved'] = improved = True
                    return RepairResult(True, deepcopy(before['missing']), deepcopy(after['missing']),
                                        tuple(soft), deepcopy(added), tuple(attempts))
                except (ValueError, TimeoutError) as error:
                    row['reason'] = str(error)
                finally:
                    if not improved:
                        _restore(board, journal, saved)
                if after is None:
                    continue
                incomplete = {event['net'] for event in events} & after['missing'].keys()
                failed.update(incomplete & cohort)
                for option in _priority_orders(order, events, incomplete, journal.owners):
                    if option in orders or len(priority) >= 2:
                        continue
                    at = next((i for i in range(orders.index(order) + 1, len(orders))
                               if orders[i] in ordinary and orders[i] not in physical
                               and orders[i] not in priority), len(orders))
                    if len(orders) >= 6:
                        if at == len(orders):
                            continue
                        orders.pop(at)
                    orders.insert(at, option)
                    priority.append(option)
                for option in _orders(target, incumbents, failed):
                    if option not in orders and len(ordinary) < 4 and len(orders) < 6:
                        orders.append(option)
                        ordinary.append(option)
                for event in events:
                    if event['net'] not in incomplete:
                        continue
                    candidate = deepcopy(journal)
                    candidate.witnesses[event['net']] = {
                        uid: witness for uid, witness in event['witnesses'].items() if uid in journal.owners}
                    try:
                        more, _ = owner_cohort(candidate, event['net'], closed)
                    except ValueError as error:
                        row.setdefault('scope_rejections', {})[event['net']] = str(error)
                    else:
                        revealed.update(more)
            if revealed <= cohort:
                break
            cohort.update(revealed)
        return RepairResult(False, deepcopy(before['missing']), deepcopy(before['missing']), attempts=tuple(attempts), reason='deadline_exhausted' if time.monotonic() >= deadline else 'finite_portfolio_exhausted')
    except TimeoutError:
        missing = deepcopy(before['missing']) if before is not None else None
        return RepairResult(False, missing, missing, attempts=tuple(attempts), reason='deadline_exhausted')
    finally:
        if not improved:
            _restore(board, journal, saved)
