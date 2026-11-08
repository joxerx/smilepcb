"""Restore a finalized trial using its actual, freshly filled plane contacts.

This is deliberately separate from pre-zone joint repair. The caller owns a
disposable board transaction: failed searches may have emitted copper. No files
are published here. Temporary copper outside ``nets`` is also caller-owned and
must be removed and checked in the caller's final complete-board proof.
"""
import time

from smilepcb.native_worker import p, snapshot
from .electrical_partitions import conductor_groups
from .joint_repair_contract import require_components
from .joint_repair_native import _restore
from .native_graph import copper_graph


def _refill(board, deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError('Finalized restoration deadline exhausted')
    board.BuildConnectivity()
    if not p.ZONE_FILLER(board).Fill(board.Zones()):
        raise ValueError('Finalized restoration refill failed')
    if time.monotonic() >= deadline:
        raise TimeoutError('Finalized restoration deadline exhausted')


def _retained_exact(board, before, removed):
    after = snapshot(board)
    if (after['footprints'] != before['footprints']
            or set(before['tracks']) - set(after['tracks']) != removed
            or any(after['tracks'].get(uid) != track for uid, track in before['tracks'].items()
                   if uid not in removed)):
        raise ValueError('Finalized restoration changed retained source copper or terminals')
    return after


def restore_finalized(board, bare_obligations, filled_obligations, original_nets,
                      removed, before, field, region, nets, deadline, reverse=False,
                      steps=None, *, movable=(), feedback=None):
    """Restore unsatisfied bare obligations, then prove full retained partitions.

    Both obligation inventories must be derived from the same original board,
    excluding declared removals; the filled inventory requires a fresh native
    fill. It describes required identities and partitions, never future plane
    geometry. All ordinary zones must be attached on entry. Their exact native
    objects are detached only during bare routing, then reattached and refilled
    even when routing raises. Rule areas remain hard throughout.

    Success also proves exact retained track/terminal geometry and attachment
    of every new track on ``nets``. This function is not an atomic copper edit;
    discard the caller's trial on failure and do final DRC before publication.
    """
    removed = set(removed)
    nets = set(nets)
    bare = [frozenset(group) for group in bare_obligations]
    filled = [frozenset(group) for group in filled_obligations]
    retained = set().union(set(), *filled)
    if (not retained or any(not group for group in bare + filled)
            or set().union(set(), *bare) != retained
            or set(original_nets) - removed != retained
            or any(original_nets[uid] not in nets for uid in retained)
            or any(len({original_nets[uid] for uid in group}) != 1 for group in bare + filled)
            or sum(map(len, bare)) != len(retained)
            or sum(map(len, filled)) != len(retained)):
        raise ValueError('Inconsistent finalized restoration obligation inventory')
    zones = [zone for zone in board.Zones() if not zone.GetIsRuleArea()]
    if not zones:
        raise ValueError('Finalized restoration requires attached ordinary zones')
    _retained_exact(board, before, removed)
    _refill(board, deadline)
    current = conductor_groups(copper_graph(board, nets, deadline))
    present = set().union(set(), *current)
    if not retained <= present:
        raise ValueError('Original retained conductor vanished')
    owner = {uid: index for index, group in enumerate(filled) for uid in group}
    if any(len({owner[uid] for uid in group & retained}) > 1 for group in current):
        raise ValueError('Original retained conductor components merged')
    pending = [group for group in bare if not any(group <= component for component in current)]
    steps = [] if steps is None else steps
    roles = {}
    if pending:
        detached = []
        try:
            for zone in zones:
                board.Remove(zone)
                detached.append(zone)
            roles, steps = _restore(board, pending, original_nets, removed, before, field,
                                    region, nets, deadline, reverse, steps,
                                    movable=movable, feedback=feedback)
        finally:
            for zone in detached:
                board.Add(zone)
            _refill(board, deadline)
    after = _retained_exact(board, before, removed)
    added = {uid for uid, track in after['tracks'].items()
             if uid not in before['tracks'] and track['net'] in nets}
    require_components(filled, conductor_groups(copper_graph(board, nets, deadline)), added=added)
    return roles, steps
