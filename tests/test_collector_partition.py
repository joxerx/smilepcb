"""Exact finite-width partition decisions; physical admission remains native."""
from copy import deepcopy
import sys
from types import ModuleType, SimpleNamespace

import pytest

from collector_partition_support import Board, Item, capacity, rail_ports


def partition(groups, ports, board=None, *, profile_gap=173000, clock=None, deadline=5.):
    ns = capacity(clock, profile_gap=profile_gap)
    assert 'partition_initial' in ns, 'Wide source rails need a bounded upfront physical partition'
    return ns['partition_initial'](board or Board(), ports, groups, deadline=deadline)


def test_wide_cohort_uses_minimax_contiguous_cut_not_count_midpoint():
    groups = [['a', 'b', 'c'], ['later-a', 'later-b']]
    ports = rail_ports(groups[0], [0, 625000, 5125000])+rail_ports(groups[1], [0, 500000])
    original = deepcopy((groups, ports))
    initial, report = partition(groups, ports)
    assert initial == [['a', 'b'], ['c'], ['later-a', 'later-b']]
    assert report['parent'] == ['a', 'b', 'c'] and report['children'] == [['a', 'b'], ['c']]
    assert report['parent_reserved_width_nm'] == 5621000
    assert report['child_reserved_widths_nm'] == [1121000, 496000]
    assert report['cut_gap_nm'] == 4500000 and report['twice_safe_pitch_nm'] == 996002
    assert (groups, ports) == original


def test_only_first_eligible_group_changes_in_place_without_resorting_children():
    groups = [['compact-a', 'compact-b', 'compact-c', 'compact-d'], ['z', 'y', 'x'], ['b', 'a']]
    ports = (rail_ports(groups[0], [0, 500000, 1000000, 1750000])+
             rail_ports(groups[1], [0, 625000, 5125000])+rail_ports(groups[2], [0, 5000000]))
    initial, report = partition(groups, ports)
    assert initial == [groups[0], ['z', 'y'], ['x'], ['b', 'a']]
    assert report['initial_index'] == 1


def test_minimax_uses_central_cut_even_when_outer_gaps_are_larger():
    groups = [['a', 'b', 'c', 'd', 'e', 'f']]
    ports = rail_ports(groups[0], [0, 4000000, 5000000, 8000000, 9000000, 13000000])
    initial, report = partition(groups, ports)
    # Largest-gap cuts leave9mm children; the3mm central gap halves both bands.
    assert initial == [['a', 'b', 'c'], ['d', 'e', 'f']]
    assert report['cut_gap_nm'] == 3000000
    assert report['child_reserved_widths_nm'] == [5496000, 5496000]


def test_compact_first_bank_is_exact_noop_without_native_index_or_metadata_scan():
    groups = [['a', 'b', 'c', 'd'], ['e'], ['f']]
    ports = rail_ports(groups[0], [0, 500000, 1000000, 1750000])+rail_ports(['e', 'f'], [0, 500000])
    initial, report = partition(groups, ports, Board(forbid_scan=True))
    assert initial == groups and report is None


@pytest.mark.parametrize('heading', [(1, 0), (-1, 0), (0, 1), (0, -1)])
@pytest.mark.parametrize('origin', [(-137, -1000001), (292, 7000001)])
def test_translation_reflection_and_renaming_preserve_physical_cut(heading, origin):
    ports = rail_ports(['omega', 'alpha', 'beta'], [0, 625000, 5125000], heading=heading, origin=origin)
    initial, report = partition([['omega', 'alpha', 'beta']], ports)
    assert initial == [['omega', 'alpha'], ['beta']]
    assert report['child_reserved_widths_nm'] == [1121000, 496000]


@pytest.mark.parametrize('shift', [-137, 0, 292])
@pytest.mark.parametrize('heading', [(1, 0), (-1, 0)])
def test_odd_span_half_width_boundary_cannot_leak_two_nanometers(shift, heading):
    ports = rail_ports(['a', 'b', 'c'], [-1000001+shift, -375000+shift, 746001+shift], heading=heading)
    initial, report = partition([['a', 'b', 'c']], ports)
    # Parent2242002; true maxchild1121002 => twice child exceeds parent by2.
    assert initial == [['a', 'b', 'c']] and report is None


def test_odd_track_width_and_span_round_separately_like_search():
    ports = rail_ports(['a', 'b', 'c'], [0, 1, 3000001])
    initial, report = partition([['a', 'b', 'c']], ports, Board(m_TrackMinWidth=150001))
    assert initial == [['a', 'b'], ['c']]
    assert report['child_reserved_widths_nm'] == [496004, 496002]
    assert report['twice_safe_pitch_nm'] == 996003


@pytest.mark.parametrize('cut_gap,eligible', [(996001, False), (996002, True)])
def test_exact_portal_pitch_threshold(cut_gap, eligible):
    groups = [['a', 'b']]
    initial, report = partition(groups, rail_ports(groups[0], [0, cut_gap]))
    assert (initial == [['a'], ['b']]) is eligible
    assert (report is not None) is eligible


def test_nominal_compression_pitch_can_dominate_portal_pitch():
    groups = [['a', 'b']]
    board = Board(m_TrackMinWidth=900000, m_MinClearance=150000)
    ports = rail_ports(groups[0], [0, 1800000])
    initial, report = partition(groups, ports, board)
    # Portal twice1746002, nominal twice2200000: below nominal must remain one group.
    assert initial == groups and report is None


def test_real_native_and_generation_floors_are_not_fixed_process_constants():
    groups = [['a', 'b']]
    ports = rail_ports(groups[0], [0, 850002])
    low, report = partition(groups, ports, Board(m_MinClearance=100000), profile_gap=0)
    high, rejected = partition(groups, ports, Board(m_MinClearance=100000), profile_gap=200000)
    assert low == [['a'], ['b']] and report['twice_safe_pitch_nm'] == 850002
    assert high == groups and rejected is None


def test_native_drill_annulus_and_via_minimum_raise_required_gap():
    groups = [['a', 'b']]
    ports = rail_ports(groups[0], [0, 1400000])
    for board in (Board(m_MinThroughDrill=800000), Board(m_ViasMinSize=1100000),
                  Board(m_ViasMinAnnularWidth=400000)):
        initial, report = partition(groups, ports, board)
        assert initial == groups and report is None


def test_clearance_uses_native_own_floor_and_excludes_only_parent_owned_tracks():
    groups = [['a', 'b', 'c']]
    ports = rail_ports(groups[0], [0, 625000, 5125000])
    board = Board(tracks=[Item('a', 'a-head', clearance=9000000), Item('foreign', 'prefix', clearance=9000000)],
                  pads=[Item('a', 'pad', clearance=0, layers=(0,))])
    _, report = partition(groups, ports, board)
    # Applying the process173000 floor before own_clearance would invent174000.
    assert report['gap_nm'] == 173000 and report['child_reserved_widths_nm'] == [1121000, 496000]
    board.tracks.append(Item('b', 'field-copper', local=2500000, layers=(2,)))
    initial, report = partition(groups, ports, board)
    assert initial == groups and report is None


def test_child_widths_use_their_own_net_clearance_not_the_parent_maximum():
    groups = [['a', 'b', 'c']]
    ports = rail_ports(groups[0], [0, 625000, 5125000])
    board = Board(pads=[Item('c', 'custom-pad', clearance=600000, layers=(2,))])
    initial, report = partition(groups, ports, board)
    assert initial == [['a', 'b'], ['c']]
    assert report['gap_nm'] == 601000
    assert report['parent_reserved_width_nm'] == 6477000
    assert report['child_reserved_widths_nm'] == [1121000, 1352000]


def test_expired_or_exhausted_metadata_scan_keeps_original_queue_and_ports():
    groups = [['a', 'b', 'c']]
    ports = rail_ports(groups[0], [0, 625000, 5125000])
    clock = SimpleNamespace(now=5.)
    clock.monotonic = lambda: clock.now
    assert partition(groups, ports, Board(forbid_scan=True), clock=clock) == (groups, None)
    clock.now = 0.
    class SlowItem(Item):
        def GetNetname(self):
            clock.now = 5.
            return super().GetNetname()
    original = deepcopy(ports)
    board = Board(tracks=[SlowItem('a', 'field')])
    assert partition(groups, ports, board, clock=clock) == (groups, None)
    assert ports == original


def test_child_search_keeps_sibling_copper_and_foreign_launches(monkeypatch):
    ns = capacity()
    groups = [['a', 'b', 'c']]
    ports = rail_ports(groups[0], [0, 625000, 5125000])
    children, _ = partition(groups, ports)
    future = rail_ports(['future'], [8000000])
    module = ModuleType('smilepcb.keyboard_routing.interface_launches')
    module.current_launches = lambda: ports+future
    module.interface_identity = lambda q: q['net']
    monkeypatch.setitem(sys.modules, module.__name__, module)
    class Router:
        def __init__(self, board, **kwargs):
            self.ignored = set(kwargs.get('ignored_copper', ()))
            self.track_width, self.minimum_clearance, self.net_clearances = 150000, 173000, {}
    class Policy:
        def __init__(self, board, live, **kwargs): self.nets = {q['net'] for q in live}
    ns.update(__name__='smilepcb.keyboard_routing.collector_capacity', __package__='smilepcb.keyboard_routing',
              LocalRouter=Router, PortPolicy=Policy)
    search = ns['Search'](Board(), ports, children[0], {n:(5000000, 0) for n in children[0]},
                          deadline=5, reach_nm=1000000, grid_nm=500000, allow_vias=True)
    assert search.world.ignored == {'a-head', 'b-head'}
    assert search.world.port_policy.nets == {'c', 'future'}
    assert search.detail.port_policy.nets == {'a', 'b', 'c'}


@pytest.mark.parametrize('heading', [(1, 0), (-1, 0), (0, -1)])
@pytest.mark.parametrize('width,expected', [(150000, 1121002), (150001, 1121004)])
def test_report_width_matches_actual_search_constructor_with_odd_native_dimensions(monkeypatch, heading, width, expected):
    ns = capacity()
    ports = rail_ports(['a', 'b', 'c'], [-1000001, -375000, 4124999], heading=heading)
    children, report = partition([['a', 'b', 'c']], ports, Board(m_TrackMinWidth=width))
    module = ModuleType('smilepcb.keyboard_routing.interface_launches')
    module.current_launches = lambda: ports
    module.interface_identity = lambda q: q['net']
    monkeypatch.setitem(sys.modules, module.__name__, module)
    class Router:
        def __init__(self, board, **kwargs):
            self.track_width, self.minimum_clearance, self.net_clearances = kwargs['track_width_nm'], 173000, {}
    ns.update(__name__='smilepcb.keyboard_routing.collector_capacity', __package__='smilepcb.keyboard_routing',
              LocalRouter=Router, PortPolicy=lambda *args, **kwargs: object())
    search = ns['Search'](Board(m_TrackMinWidth=width), ports, children[0],
                          {n:(5000000, 5000000) for n in children[0]},
                          deadline=5, reach_nm=1000000, grid_nm=500000, allow_vias=True)
    assert search.reserved_width == report['child_reserved_widths_nm'][0] == expected
