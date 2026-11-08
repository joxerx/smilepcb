"""Bounded fixed-core connector placement using the native USB proof."""
import hashlib
import json
import time
from dataclasses import asdict

from smilepcb.native_worker import p, snapshot
from smilepcb.keyboard_native import verify_usb_pair
from smilepcb.keyboard_routing.usb import route_controller_usb


def _fingerprint(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def _pose(connector, board):
    point = connector.GetPosition()
    return {'origin_nm': [point.x, point.y],
            'rotation_deg': connector.GetOrientationDegrees(),
            'side': board.GetLayerName(connector.GetLayer())}


def _candidates(initial, preferred_nm):
    """Coarse local coverage first, then the existing half-mm placement grid."""
    x, y = initial['origin_nm']; angle = initial['rotation_deg']
    yield x, y, angle
    seen = {(x, y, angle)}
    cx, cy = (round(v / 500000) * 500000 for v in preferred_nm)
    for step in (2000000, 500000):
        offsets = sorted((abs(dx)+abs(dy), dx, dy)
                         for dx in range(-12000000, 12000001, step)
                         for dy in range(-12000000, 12000001, step))
        for _, dx, dy in offsets:
            for turn in (0, 180, 90, 270):
                candidate = (cx+dx, cy+dy, (angle+turn) % 360)
                if candidate not in seen:
                    seen.add(candidate)
                    yield candidate


def _acceptance_context(board,*,max_length_nm=...):
    from smilepcb.usb_policy import resolve_usb_max_length_nm
    max_length_nm=resolve_usb_max_length_nm(max_length_nm)
    from smilepcb.keyboard_routing.generation import current_generation_profile
    from smilepcb.routing_quality import current_quality
    rules = board.GetDesignSettings()
    return {'max_length_nm':max_length_nm,'rules': {key: getattr(rules, key) for key in (
                'm_MinClearance', 'm_HoleClearance', 'm_TrackMinWidth',
                'm_CopperEdgeClearance', 'm_MinThroughDrill', 'm_ViasMinSize',
                'm_ViasMinAnnularWidth')},
            'copper_layers': board.GetCopperLayerCount(),
            'generation': asdict(current_generation_profile()),
            'quality': asdict(current_quality())}


def verify_prepared_usb(board, prepared, *, deadline, max_length_nm=...):
    """Keep proved source geometry and recheck current physical constraints."""
    from smilepcb.usb_policy import DEFAULT_USB_MAX_LENGTH_NM,resolve_usb_max_length_nm
    max_length_nm=resolve_usb_max_length_nm(max_length_nm)
    if time.monotonic() >= deadline:
        raise TimeoutError('Prepared USB proof deadline exhausted')
    if not prepared.get('accepted') or prepared.get('board_state_sha256') != _fingerprint(snapshot(board)):
        raise ValueError('Prepared USB geometry changed after native proof')
    recorded_context=prepared.get('acceptance_context')
    if not isinstance(recorded_context,dict):raise ValueError('Prepared USB acceptance context is missing')
    recorded_context=dict(recorded_context)
    recorded_context.setdefault('max_length_nm',DEFAULT_USB_MAX_LENGTH_NM)
    resolve_usb_max_length_nm(recorded_context['max_length_nm'])
    if recorded_context != _acceptance_context(board,max_length_nm=max_length_nm):
        raise ValueError('Prepared USB acceptance context changed after native proof')
    if verify_usb_pair(board,max_length_nm=max_length_nm) != prepared['verified_lengths_mm']:
        raise ValueError('Prepared USB lengths changed after native proof')
    # snapshot deliberately omits artwork, custom pad shapes, local clearances,
    # milling and zones. Recheck actual generated copper against a fresh native
    # index instead of serializing native shapes into an incomplete receipt.
    from smilepcb.keyboard_routing.local_router import LocalRouter
    from smilepcb.keyboard_routing.native_graph import copper_graph
    router = LocalRouter(board, deadline=deadline, compact=True)
    owned = set(prepared['added_ids'])
    for track in board.GetTracks():
        if track.m_Uuid.AsString() not in owned:
            continue
        if router.expired():
            raise TimeoutError('Prepared USB physical proof deadline exhausted')
        if not isinstance(track, p.PCB_TRACK) or isinstance(track, (p.PCB_VIA, p.PCB_ARC)):
            raise ValueError('Prepared USB contains unsupported generated copper')
        shape = p.SHAPE_SEGMENT(track.GetStart(), track.GetEnd(), track.GetWidth())
        if track.GetWidth() < board.GetDesignSettings().m_TrackMinWidth or not router.clear(shape, track.GetLayer(), track.GetNetname()):
            raise ValueError('Prepared USB violates current native geometry or clearance')
    graph = copper_graph(board, {'USB_DP', 'USB_DM'}, deadline)
    for net in ('USB_DP', 'USB_DM'):
        groups = [rows for rows in graph['groups'].values() if graph['nodes'][rows[0]]['net'] == net]
        if len(groups) != 1:
            raise ValueError('Prepared USB current native connectivity is incomplete')
        refs = {graph['nodes'][i]['item'].GetParentFootprint().GetReference()
                for i in groups[0] if isinstance(graph['nodes'][i]['item'], p.PAD)}
        if not {'U_MCU', 'J_UDB'} <= refs:
            raise ValueError('Prepared USB current native endpoints are missing')
    if router.expired():
        raise TimeoutError('Prepared USB physical proof deadline exhausted')
    return prepared


def select_usb_placement(board, connector, cfg, *, preferred_nm, legal, deadline, fixed_pose=None, max_length_nm=...):
    """Keep a proved pair; otherwise restore the entry pose and copper exactly.

    ``legal`` is the placement worker's native board/courtyard/pad/hole check,
    rebuilt from the final fixed footprints and imported copper before entry.
    The caller supplies an absolute deadline within its existing placement cap.
    """
    from smilepcb.usb_policy import resolve_usb_max_length_nm
    max_length_nm=resolve_usb_max_length_nm(max_length_nm)
    before = snapshot(board); initial = _pose(connector, board)
    if fixed_pose is not None and initial != fixed_pose:
        raise ValueError('Fixed USB pose differs from actual mechanical connector')
    uid = connector.m_Uuid.AsString()
    part = next(q for q in cfg['parts'] if q['ref'] == 'J_UDB')
    result = {'accepted': False, 'initial_pose': initial, 'trials': [],
              'geometry_trials': 0, 'illegal_poses': 0,
              'fixed_footprints_sha256': _fingerprint({u:s for u,s in before['footprints'].items() if u != uid}),
              'imported_copper_sha256': _fingerprint(before['tracks'])}

    def restore():
        for track in list(board.GetTracks()):
            if track.m_Uuid.AsString() not in before['tracks']:
                board.Delete(track)
        connector.SetOrientationDegrees(initial['rotation_deg'])
        connector.SetPosition(p.VECTOR2I(*initial['origin_nm']))
        board.BuildConnectivity()
        if snapshot(board) != before:
            raise ValueError('USB placement rollback changed source')

    try:
        candidates=iter([(*initial['origin_nm'],initial['rotation_deg'])]) if fixed_pose is not None else _candidates(initial,preferred_nm)
        for x, y, angle in candidates:
            if time.monotonic() >= deadline:
                result['reason'] = 'USB placement deadline exhausted'; break
            if len(result['trials']) >= 32:
                result['reason'] = 'USB placement routing trial limit exhausted'; break
            connector.SetOrientationDegrees(angle)
            connector.SetPosition(p.VECTOR2I(x, y))
            result['geometry_trials'] += 1
            if not legal(connector):
                result['illegal_poses'] += 1
                continue
            trial = {'pose': _pose(connector, board), 'accepted': False}
            result['trials'].append(trial)
            began = time.monotonic()
            proof = route_controller_usb(board, deadline=min(deadline, began+4),max_length_nm=max_length_nm)
            trial.update(accepted=proof['accepted'], elapsed_s=time.monotonic()-began)
            if not proof['accepted']:
                trial['reason'] = proof.get('reason', 'USB proof rejected')
                restore()
                continue
            after = snapshot(board)
            if any(after['footprints'].get(u) != s for u,s in before['footprints'].items() if u != uid) or any(after['tracks'].get(u) != s for u,s in before['tracks'].items()):
                raise ValueError('USB placement changed fixed source geometry')
            selected = _pose(connector, board)
            trial['verified_lengths_mm'] = proof['verified_lengths_mm']
            proof['board_state_sha256'] = _fingerprint(after)
            proof['acceptance_context'] = _acceptance_context(board,max_length_nm=max_length_nm)
            result.update(selected_pose=selected, usb=proof,
                          verified_lengths_mm=proof['verified_lengths_mm'])
            if time.monotonic() >= deadline:
                result['reason'] = 'USB placement proof deadline exhausted'; break
            part.update(pcb=[x/1e6, y/1e6], rotation=selected['rotation_deg'], side=selected['side'])
            result['accepted'] = True
            return result
        result.setdefault('reason', 'No accepted USB pose in bounded local search')
        return result
    finally:
        if not result['accepted']:
            restore()
