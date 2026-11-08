"""Exercise native return geometry under a deterministic setup budget."""
import json
from unittest.mock import patch

from smilepcb.keyboard_routing import power, safety
from smilepcb.keyboard_routing.local_router import LocalRouter, vec
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.native_worker import p, snapshot


def track(board, net, start, end, layer=p.B_Cu):
    item = p.PCB_TRACK(board)
    item.SetStart(vec(start))
    item.SetEnd(vec(end))
    item.SetWidth(150000)
    item.SetLayer(layer)
    item.SetNet(board.FindNet(net))
    item.SetLocked(True)
    board.Add(item)


def fixture():
    board = p.BOARD()
    board.SetCopperLayerCount(2)
    rules = board.GetDesignSettings()
    rules.m_MinClearance = rules.m_TrackMinWidth = 150000
    for net in ('GND', 'USB_DP', 'USB_DM'):
        board.Add(p.NETINFO_ITEM(board, net))
    corners = [(0, 0), (20000000, 0), (20000000, 20000000), (0, 20000000)]
    for a, z in zip(corners, corners[1:] + corners[:1]):
        edge = p.PCB_SHAPE(board)
        edge.SetShape(p.SHAPE_T_SEGMENT)
        edge.SetStart(vec(a))
        edge.SetEnd(vec(z))
        edge.SetLayer(p.Edge_Cuts)
        edge.SetWidth(50000)
        board.Add(edge)
    pads = []
    for index, x in enumerate((4000000, 6000000, 9000000, 13000000)):
        footprint = p.FOOTPRINT(board)
        footprint.SetReference(f'U{index}')
        footprint.SetLayer(p.B_Cu)
        board.Add(footprint)
        pad = p.PAD(footprint)
        pad.SetNumber('1')
        pad.SetAttribute(p.PAD_ATTRIB_SMD)
        pad.SetShape(p.PAD_SHAPE_RECT)
        layers = p.LSET()
        layers.AddLayer(p.B_Cu)
        pad.SetLayerSet(layers)
        pad.SetPosition(vec((x, 5000000)))
        pad.SetSize(vec((300000, 300000)))
        pad.SetNet(board.FindNet('GND'))
        footprint.Add(pad)
        pads.append(pad)
    track(board, 'GND', (4000000, 5000000), (6000000, 5000000))
    track(board, 'USB_DP', (4000000, 15000000), (9000000, 15000000))
    track(board, 'USB_DM', (4000000, 15500000), (9000000, 15500000))
    return board, pads


def run(mode):
    board, pads = fixture()
    if mode in ('collision', 'collision_expiry'):
        # Keep a genuinely invalid original via for the independent validator.
        via = p.PCB_VIA(board)
        via.SetPosition(pads[-1].GetPosition())
        via.SetWidth(500000)
        via.SetDrill(200000)
        via.SetLayerPair(p.F_Cu, p.B_Cu)
        via.SetNet(board.FindNet('GND'))
        board.Add(via)
    if mode == 'merge':
        # An isolated pad's first lead reaches this original track, which
        # already contacts the later pad but has no via of its own.
        track(board, 'GND', (9400000, 5000000), (13000000, 5000000))
        graph = copper_graph(board, {'GND'}, include_zones=False)
        roots = {node['item'].m_Uuid.AsString(): graph['find'](i)
                 for i, node in enumerate(graph['nodes'])}
        assert roots[pads[-2].m_Uuid.AsString()] != roots[pads[-1].m_Uuid.AsString()]
    before = snapshot(board)
    elapsed = 0

    def router(*args, **kwargs):
        nonlocal elapsed
        elapsed += 16 if mode == 'expiry' else 6
        return LocalRouter(*args, **kwargs)

    original_verify = safety.require_via_land_clearance
    original_collide = p.SHAPE.Collide

    def collide(*args, **kwargs):
        nonlocal elapsed
        collision = original_collide(*args, **kwargs)
        if collision:
            elapsed = 16
        return collision

    def verify(*args, **kwargs):
        nonlocal elapsed
        assert len(snapshot(board)['tracks']) > len(before['tracks'])
        if mode == 'late_exception':
            elapsed = 16
        if mode in ('exception', 'late_exception'):
            raise ValueError('forced final verification failure')
        if mode == 'late_expiry':
            elapsed = 16
        if mode == 'collision_expiry':
            with patch.object(p.SHAPE, 'Collide', staticmethod(collide)):
                return original_verify(*args, **kwargs)
        return original_verify(*args, **kwargs)

    result = {}
    with (patch.object(power.time, 'monotonic', lambda: elapsed),
          patch.object(power, 'LocalRouter', router),
          patch.object(safety, 'require_via_land_clearance', verify)):
        try:
            result = power.seed_controller_returns(
                board, controller_refs={'U0', 'U1', 'U2', 'U3'}, plane_nets={'GND'},
                deadline=15, via_to_smd_clearance_nm=200000)
        except (TimeoutError, ValueError) as error:
            result = {'exception': type(error).__name__, 'reason': str(error)}
    after = snapshot(board)
    graph = copper_graph(board, {'GND'}, include_zones=False)
    ids = {node['item'].m_Uuid.AsString(): i for i, node in enumerate(graph['nodes'])}
    groups = {}
    for pad in pads:
        uid = pad.m_Uuid.AsString()
        groups[uid] = graph['find'](ids[uid])
    ports = result.get('ports', [])
    added = set(after['tracks']) - set(before['tracks'])
    return {
        'accepted': result.get('accepted', False), 'reason': result.get('reason'),
        'exception': result.get('exception'), 'rollback_exact': after == before,
        'source_exact': before['footprints'] == after['footprints'] and all(
            after['tracks'].get(uid) == shape for uid, shape in before['tracks'].items()),
        'reported_copper_exact': added == set(result.get('added_ids', [])) ==
            {uid for port in ports for uid in port['ids']},
        'later_reuses_earlier_via': len(ports) == 3
            and ports[-1]['pad_ids'] == [pads[-1].m_Uuid.AsString()]
            and ports[-1]['reused'] and ports[-1]['ids'] == []
            and ports[-1]['via_id'] == ports[-2]['via_id'],
        'island_sizes': sorted(len(port['pad_ids']) for port in ports),
        'all_pads_reach_real_vias': {uid for port in ports for uid in port['pad_ids']} == set(groups)
            and all(isinstance(graph['nodes'][ids[port['via_id']]]['item'], p.PCB_VIA)
                    and all(groups[uid] == graph['find'](ids[port['via_id']]) for uid in port['pad_ids'])
                    for port in ports),
    }


report = {mode: run(mode) for mode in (
    'success', 'expiry', 'late_expiry', 'exception', 'late_exception', 'collision', 'collision_expiry', 'merge')}
board, pads = fixture()
before = snapshot(board)
rejected = []
for options in ({'max_distance_nm': 799999}, {'via_to_smd_clearance_nm': -1}):
    try:
        power.seed_plane_access(board, pads, **options)
    except ValueError:
        rejected.append(True)
report['validation'] = {'rejected': rejected, 'source_exact': snapshot(board) == before}
print(json.dumps(report))
