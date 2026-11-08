"""Witnesses come from complete physical carrier collisions, never net guesses."""
import ast
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from smilepcb.keyboard_routing import source_attachment_witnesses as witnesses
from smilepcb.keyboard_routing.corridor_geometry import offset_path
from smilepcb.keyboard_routing.follow_geometry import simplify


class Item:
    def __init__(self, uid, net='earth', kind='PCB_TRACK', locked=False):
        self.m_Uuid = SimpleNamespace(AsString=lambda: uid)
        self.net, self.kind, self.locked = net, kind, locked
    def GetNetname(self): return self.net
    def GetClass(self): return self.kind
    def IsLocked(self): return self.locked


class Board:
    def __init__(self, hits):
        self.hits = hits
        self.items = [Item('source-a', 'pulse-a'), Item('source-b', 'pulse-b'),
                      Item('plane-a'), Item('plane-b')]
        self.zones = [SimpleNamespace(GetIsRuleArea=lambda: False, GetNetname=lambda: 'earth',
                                     m_Uuid=SimpleNamespace(AsString=lambda: 'zone-earth'))]
        self.block_body = False
    def GetTracks(self): return self.items
    def Zones(self): return self.zones


class World:
    minimum_clearance = 150000
    via_width = 500000
    drill = 200000
    path_policy = None
    def __init__(self, board, **kwargs):
        self.board = board; self.net_clearances = {}; self.track_width = kwargs['track_width_nm']
    def add_path(self, *args): pass
    def add_via(self, *args): pass
    def clear(self, *args): return not self.board.block_body
    def via_clear(self, *args): return True
    def collisions(self, shape, face, net):
        hits = self.board.hits.get(net, ())
        return set(hits(shape) if callable(hits) else hits)


@pytest.fixture
def native_boundary(monkeypatch):
    native = SimpleNamespace(F_Cu=0, B_Cu=2, SHAPE_SEGMENT=lambda a, z, width: (a, z, width))
    def snapshot(board):
        return dict(tracks={item.m_Uuid.AsString(): dict(net=item.net, **{'class': item.kind},
                    start=(0, 0), width=500000, drill=200000) for item in board.items}, footprints={})
    root = Path(__file__).resolve().parents[1] / 'smilepcb/keyboard_routing'
    namespace = dict(clean=simplify, offset_path=offset_path, p=native, vec=lambda q: q)
    source = root / 'obstacle_bundles.py'
    for name in ('_expanded', '_add_checked'):
        node = next(node for node in ast.parse(source.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    monkeypatch.setitem(sys.modules, 'smilepcb.native_worker', SimpleNamespace(p=native, snapshot=snapshot))
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.local_router', SimpleNamespace(vec=lambda q: q))
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.virtual_copper', SimpleNamespace(VirtualCopper=World))
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.obstacle_bundles', SimpleNamespace(
        _expanded=namespace['_expanded'], _add_checked=namespace['_add_checked']))


def run(board, protected=()):
    group = [dict(net=net, ids=['source-'+suffix], width=150000, heading=(-1, 0),
                  pad_position=(4000000, y), window=dict(source=(4000000, y), source_layer=0,
                  fixed_legs=[], fixed_vias=[])) for net, suffix, y in
             [('pulse-a', 'a', -300000), ('pulse-b', 'b', 300000)]]
    axis = [dict(kind='track', layer=0, end_layer=0, start=(2000000, 0), end=(0, 0))]
    return witnesses.source_attachment_witnesses(board, group, axis, bounds=(-1000000, -2000000, 5000000, 2000000),
                                                protected_ids=protected, deadline=time.monotonic()+2)


def test_all_actual_plane_track_collisions_are_preserved_as_one_carrier_witness(native_boundary):
    result = run(Board({'pulse-a': lambda shape: {'plane-a'} if shape[0] == (4000000, -300000) else {'plane-b'}}))
    assert result['witness_ids'] == ['plane-a', 'plane-b']
    carrier = next(row for row in result['carriers'] if row['net'] == 'pulse-a')
    assert carrier['collision_ids'] == ['plane-a', 'plane-b']
    assert carrier['eligible']


@pytest.mark.parametrize('hard', ['ghost', 'missing', 'source-b'])
def test_mixed_unknown_or_ghost_collision_invalidates_entire_carrier(native_boundary, hard):
    result = run(Board({'pulse-a': {'plane-a', hard}}))
    assert result['witness_ids'] == []
    carrier = next(row for row in result['carriers'] if row['net'] == 'pulse-a')
    assert carrier['collision_ids'] == sorted({'plane-a', hard})
    assert not carrier['eligible']


@pytest.mark.parametrize('change', ['locked', 'protected', 'via', 'no-plane-zone', 'rule-area-only'])
def test_ineligible_plane_like_item_never_becomes_removal_witness(native_boundary, change):
    board = Board({'pulse-a': {'plane-a'}}); protected = ()
    if change == 'locked': board.items[2].locked = True
    if change == 'protected': protected = {'plane-a'}
    if change == 'via': board.items[2].kind = 'PCB_VIA'
    if change == 'no-plane-zone': board.zones = []
    if change == 'rule-area-only': board.zones[0].GetIsRuleArea = lambda: True
    assert run(board, protected)['witness_ids'] == []


def test_blocked_body_prevents_source_witness_collection(native_boundary):
    board = Board({'pulse-a': {'plane-a'}}); board.block_body = True
    result = run(board)
    assert result['witness_ids'] == []
    assert result['carriers'] == []
