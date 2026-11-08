"""Real controller generator accounting; CAD boundaries are host doubles."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from smilepcb.keyboard_routing.bank_geometry import clean_port_path
from smilepcb.keyboard_routing.conditional_sources import SourceSearchBudget


ROOT = Path(__file__).resolve().parents[1]


def _functions(path, names, scope):
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)


class Pad:
    def __init__(self, uid, net, number, footprint):
        self.m_Uuid = SimpleNamespace(AsString=lambda: uid)
        self.net, self.number, self.footprint = net, number, footprint
        self.point = (1000000, 0)

    def GetNetname(self): return self.net
    def GetNumber(self): return self.number
    def GetParentFootprint(self): return self.footprint
    def GetPosition(self): return self.point
    def GetAttribute(self): return 1
    def IsOnLayer(self, layer): return layer == 0
    def GetEffectiveShape(self, layer):
        x, y = self.point
        return SimpleNamespace(BBox=lambda: SimpleNamespace(
            GetLeft=lambda: x-100000, GetRight=lambda: x+100000,
            GetTop=lambda: y-100000, GetBottom=lambda: y+100000))


class Copper:
    def __init__(self, uid, net, shape):
        self.m_Uuid = SimpleNamespace(AsString=lambda: uid)
        self.net, self.shape = net, shape


def scenario(*, aliases=('a', 'alias'), second_net=False, fail_second='empty'):
    """Two connected MCU aliases, optionally followed by an empty legal domain."""
    pads, tracks, queries = [], [], []
    footprint = SimpleNamespace(GetReference=lambda: 'MCU', Pads=lambda: pads,
                                GetPosition=lambda: (0, 0))
    pads.extend(Pad(uid, 'alpha', str(i+1), footprint) for i, uid in enumerate(aliases))
    if second_net:
        pads.append(Pad('b', 'beta', '3', footprint))
    board = SimpleNamespace(GetFootprints=lambda: [footprint], GetTracks=lambda: list(tracks),
                            Zones=lambda: [], Delete=tracks.remove,
                            GetDesignSettings=lambda: SimpleNamespace(
                                m_MinClearance=150000, m_TrackMinWidth=150000))

    def snapshot(board):
        return {'footprints': {'MCU': [(q.m_Uuid.AsString(), q.net) for q in pads]},
                'tracks': {q.m_Uuid.AsString(): (q.net, q.shape) for q in tracks}}

    def graph(board, nets, deadline):
        nodes = [{'item': q, 'net': q.net} for q in [*pads, *tracks] if q.net in nets]
        groups = {}
        for i, node in enumerate(nodes):
            groups.setdefault(node['net'], []).append(i)
        return {'nodes': nodes, 'groups': groups,
                'find': lambda i: nodes[i]['net']}

    class Router:
        def __init__(self, board, **kwargs):
            self.minimum_clearance = 150000
            self.net_clearances = {}
            self.track_width, self.via_width, self.drill = 150000, 600000, 300000
            self.added = []

        def via_clear(self, point, net):
            queries.append(('via', net))
            if net == 'beta' and fail_second is not None:
                if fail_second == 'timeout':
                    raise TimeoutError('controlled second-domain native deadline')
                return False
            return True

        def clear(self, shape, layer, net):
            queries.append(('clear', net))
            return True

        def add_path(self, path, layer, net):
            for a, b in zip(path, path[1:]):
                self._add(net, ('track', a, b, layer))

        def add_via(self, point, net):
            self._add(net, ('via', point))

        def _add(self, net, shape):
            uid = 'new-%d' % len(tracks)
            tracks.append(Copper(uid, net, shape))
            self.added.append(uid)

    scope = {'__name__': 'smilepcb.keyboard_routing._host_source_budget',
             '__package__': 'smilepcb.keyboard_routing', 'math': math,
             'time': SimpleNamespace(monotonic=lambda: 0.),
             'p': SimpleNamespace(PAD=Pad, PAD_ATTRIB_SMD=1, F_Cu=0, B_Cu=2,
                 SHAPE_SEGMENT=lambda a, b, width: (a, b, width),
                 SHAPE_CIRCLE=lambda point, radius: (point, radius),FromMM=lambda value:round(value*1000000),
                 SHAPE=SimpleNamespace(Collide=lambda a, b, gap: False)),
             'snapshot': snapshot, 'copper_graph': graph, 'LocalRouter': Router,
             'xy': tuple, 'vec': tuple, 'distance': math.dist,
             'clean_port_path': clean_port_path,
             '_compatible_ports': lambda *args, **kwargs: True,
             'compatible_bank': lambda a, b: True,
             'require_via_land_clearance': lambda board, deadline: None}
    _functions(ROOT/'smilepcb/routing_style.py', {'octile_paths'}, scope)
    _functions(ROOT/'smilepcb/keyboard_routing/controller_ports.py',
               {'route_controller_ports', '_select', '_select_access_domains','_coherent_via_candidate'}, scope)

    def run(**kwargs):
        options = {'max_candidates': 2, 'max_distance_nm': 800000}
        options.update(kwargs)
        return scope['route_controller_ports'](
            board, controller_refs={'MCU'}, mcu_refs={'MCU'}, deadline=10.,
            **options)

    return SimpleNamespace(run=run, queries=queries, tracks=tracks,
                           snapshot=lambda: snapshot(board),board=board,pads=pads,footprint=footprint,scope=scope)


def test_default_and_explicit_none_keep_same_emission_and_search():
    implicit, explicit = scenario(), scenario()
    before = implicit.snapshot()
    left, right = implicit.run(), explicit.run(source_budget=None)
    assert left == right
    assert left['accepted'] and left['candidate_counts'] == [2] and left['states'] == 1
    assert 'source_budget' not in left
    assert implicit.snapshot() == explicit.snapshot() != before
    assert implicit.queries == explicit.queries


@pytest.mark.parametrize('failure', ['empty', 'timeout'])
def test_later_domain_failure_keeps_prior_candidate_charge(failure):
    shared = SourceSearchBudget([('a', 'alias'), ('b',)], max_candidates=3)
    case = scenario(second_net=True, fail_second=failure)
    before = case.snapshot()
    report = case.run(source_budget=shared)
    assert not report['accepted'] and report['ports'] == [] and report['added_ids'] == []
    assert case.snapshot() == before
    if failure == 'empty':
        assert report['missing_net'] == 'beta'
    else:
        assert report['reason'] == 'controlled second-domain native deadline'
    assert report['source_budget']['candidate_domains'] == [
        {'pad_ids': ['a', 'alias'], 'spent': 2, 'remaining': 1},
        {'pad_ids': ['b'], 'spent': 0, 'remaining': 3}]
    assert report['source_budget']['states_spent'] == 0


def test_reordered_aliases_share_remaining_domain_then_skip_exhausted_search():
    shared = SourceSearchBudget([('a', 'alias')], max_candidates=3)
    first = scenario(aliases=('alias', 'a')).run(source_budget=shared)
    second = scenario(aliases=('a', 'alias')).run(source_budget=shared)
    assert first['accepted'] and first['candidate_counts'] == [2]
    assert second['accepted'] and second['candidate_counts'] == [1]
    assert second['source_budget']['candidate_domains'] == [
        {'pad_ids': ['a', 'alias'], 'spent': 3, 'remaining': 0}]
    exhausted = scenario(aliases=('alias', 'a'))
    before = exhausted.snapshot()
    third = exhausted.run(source_budget=shared)
    assert not third['accepted'] and third['budget_exhausted']
    assert exhausted.queries == [] and exhausted.snapshot() == before
    assert third['source_budget']['states_spent'] == 2


def test_incomplete_alias_group_cannot_acquire_a_new_generator_allowance():
    shared = SourceSearchBudget([('a', 'alias')], max_candidates=3)
    case = scenario(aliases=('a',))
    before = case.snapshot()
    with pytest.raises(ValueError):
        case.run(source_budget=shared)
    assert case.queries == [] and case.snapshot() == before
    assert shared.receipt()['candidate_domains'] == [
        {'pad_ids': ['a', 'alias'], 'spent': 0, 'remaining': 3}]


@pytest.mark.parametrize('requested', [1, 256, 257, 384, 768, 1024])
def test_candidate_limit_accepts_expanded_range_without_bypassing_shared_budget(requested):
    shared = SourceSearchBudget([('a', 'alias')], max_candidates=2)
    report = scenario().run(max_candidates=requested, source_budget=shared)
    assert report['accepted']
    assert report['candidate_counts'] == [1 if requested == 1 else 2]
    assert shared.receipt()['candidate_domains'] == [
        {'pad_ids': ['a', 'alias'], 'spent': 1 if requested == 1 else 2,
         'remaining': 1 if requested == 1 else 0}]


@pytest.mark.parametrize('requested', [0, -1, 1025, True, 384.0])
def test_invalid_candidate_limit_is_rejected_before_search_or_board_changes(requested):
    case = scenario()
    before = case.snapshot()
    with pytest.raises(ValueError, match='dimensions/search limits'):
        case.run(max_candidates=requested)
    assert case.queries == []
    assert case.snapshot() == before


@pytest.mark.parametrize('requested, charged, expected', [(2, 0, 2), (768, 2, 3)])
def test_effective_candidate_limit_is_minimum_of_request_and_remaining_budget(
        requested, charged, expected):
    shared = SourceSearchBudget([('a', 'alias')], max_candidates=5)
    shared.charge_candidates(('a', 'alias'), charged)
    report = scenario().run(max_candidates=requested, max_distance_nm=2400000,
                            source_budget=shared)
    assert report['accepted']
    assert report['candidate_counts'] == [expected]
    assert shared.receipt()['candidate_domains'] == [
        {'pad_ids': ['a', 'alias'], 'spent': charged + expected,
         'remaining': 5 - charged - expected}]
