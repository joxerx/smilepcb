"""Small real-filled-plane controls; no full keyboard route is run."""
import json
import sys
import time

from smilepcb.native_worker import p, snapshot
from smilepcb.keyboard_routing import joint_repair_native as repair
from smilepcb.keyboard_routing.electrical_partitions import conductor_groups
from smilepcb.keyboard_routing.joint_repair_contract import component_obligations
from smilepcb.keyboard_routing.native_graph import copper_graph
from smilepcb.keyboard_routing.local_router import vec
from smilepcb.keyboard_routing.finalized_restoration import restore_finalized


def make(split=False):
    board = p.BOARD()
    board.SetCopperLayerCount(2)
    board.Add(p.NETINFO_ITEM(board, 'return'))
    rules = board.GetDesignSettings()
    rules.m_MinClearance = 150000
    rules.m_TrackMinWidth = 150000
    points = [(0, 0), (20000000, 0), (20000000, 10000000), (0, 10000000)]
    for a, b in zip(points, points[1:] + points[:1]):
        edge = p.PCB_SHAPE(board)
        edge.SetShape(p.SHAPE_T_SEGMENT)
        edge.SetLayer(p.Edge_Cuts)
        edge.SetStart(vec(a)); edge.SetEnd(vec(b)); edge.SetWidth(50000)
        board.Add(edge)
    tracks = []
    for a, b in [((3000000, 5000000), (5000000, 5000000)),
                 ((5000000, 5000000), (15000000, 5000000)),
                 ((15000000, 5000000), (17000000, 5000000))]:
        track = p.PCB_TRACK(board)
        track.SetNet(board.FindNet('return')); track.SetLayer(p.F_Cu)
        track.SetWidth(150000); track.SetStart(vec(a)); track.SetEnd(vec(b))
        board.Add(track); tracks.append(track)
    ranges = [(1000000, 9000000), (11000000, 19000000)] if split else [(1000000, 19000000)]
    for left, right in ranges:
        zone = p.ZONE(board)
        zone.SetLayer(p.F_Cu); zone.SetNet(board.FindNet('return'))
        zone.SetLocalClearance(150000); zone.SetMinThickness(150000)
        zone.SetPadConnection(p.ZONE_CONNECTION_FULL)
        zone.SetIslandRemovalMode(p.ISLAND_REMOVAL_MODE_NEVER)
        outline = zone.Outline(); outline.NewOutline()
        for point in [(left, 1000000), (right, 1000000), (right, 9000000), (left, 9000000)]:
            outline.Append(*point)
        board.Add(zone)
    board.BuildConnectivity()
    assert p.ZONE_FILLER(board).Fill(board.Zones())
    return board, tracks


def run(case):
    board, tracks = make(case in ('split', 'repaired', 'merged'))
    owners = [board.GetProject(), board.GetDesignSettings(), *tracks, *board.Zones()]
    deadline = time.monotonic() + 12
    if case == 'merged':
        board.Remove(tracks[1])
        board.BuildConnectivity()
        assert p.ZONE_FILLER(board).Fill(board.Zones())
    before = snapshot(board)
    graph = copper_graph(board, {'return'}, deadline, include_zones=False)
    removed = set() if case == 'merged' else {tracks[1].m_Uuid.AsString()}
    bare = component_obligations(conductor_groups(graph), removed)
    filled = component_obligations(conductor_groups(copper_graph(board, {'return'}, deadline)), removed)
    original_nets = {node['item'].m_Uuid.AsString(): node['net'] for node in graph['nodes']}
    if case == 'merged':
        board.Add(tracks[1])
    else:
        board.Remove(tracks[1])
    zone_definitions = sorted((zone.m_Uuid.AsString(), zone.Outline().Format(False),
                               zone.GetNetname(), zone.GetLayerSet().FmtHex()) for zone in board.Zones())
    if case == 'missing':
        board.Remove(tracks[0])
    if case == 'nozone':
        for zone in list(board.Zones()):
            board.Remove(zone)
    if case == 'detached':
        track = p.PCB_TRACK(board)
        track.SetNet(board.FindNet('return')); track.SetLayer(p.B_Cu)
        track.SetWidth(150000); track.SetStart(vec((8000000, 7000000))); track.SetEnd(vec((9000000, 7000000)))
        board.Add(track); owners.append(track)
    if case == 'changed':
        tracks[0].SetWidth(300000)
    if case == 'inventory':
        filled = [frozenset({tracks[0].m_Uuid.AsString()})]
    region = repair._Region([(30000000, 30000000), (31000000, 31000000)], padding=100000)
    if case == 'repaired':
        region = repair._Region([(3000000, 5000000), (17000000, 5000000)], padding=2000000)
    steps = []
    try:
        roles, steps = restore_finalized(board, bare, filled, original_nets, removed,
                                        before, {'roles': {}}, region, {'return'}, deadline, steps=steps)
        return {'accepted': True, 'added': len(set(snapshot(board)['tracks']) - set(before['tracks'])),
                'steps': steps, 'zone_count': len(board.Zones()),
                'zone_definitions_exact': zone_definitions == sorted(
                    (zone.m_Uuid.AsString(), zone.Outline().Format(False), zone.GetNetname(),
                     zone.GetLayerSet().FmtHex()) for zone in board.Zones())}
    except (ValueError, TimeoutError) as error:
        return {'accepted': False, 'error': str(error), 'zone_count': len(board.Zones())}


if __name__ == '__main__':
    print(json.dumps({case: run(case) for case in (
        'plane', 'nozone', 'split', 'missing', 'detached', 'repaired', 'merged', 'changed', 'inventory')}))
