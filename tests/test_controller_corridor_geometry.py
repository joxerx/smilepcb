"""Physical grouping and bounded, geometry-only controller corridor proposals."""
from copy import deepcopy
import json
import math
import time

import pytest

from smilepcb.keyboard_routing import controller_corridor_geometry as geometry
from smilepcb.keyboard_routing.bundle_geometry import _parallel_intervals, _point_distance
from smilepcb.keyboard_routing.polish_geometry import valid_shape


def fixture(names=('outer', 'closed', 'other'), shift=(0, 0)):
    def p(x, y):
        return (x + shift[0], y + shift[1])
    ports = [dict(net=net, exit_nm=p(2000000, 3000000+i*500000),
                  heading_origin_nm=p(1000000, 3000000+i*500000),
                  exit_layer=2, bank=['chip', 0, 1], pad_ids=[f's{i}'])
             for i, net in enumerate(names)]
    targets = {net: [dict(point=p(24000000, 9000000+i*500000), layers=[0, 2], pad_id=f't{i}'),
                     dict(point=p(-4000000, 12000000+i*500000), layers=[0], pad_id=f'b{i}')]
               for i, net in enumerate(names)}
    return ports, targets


def proposals(group, **kwargs):
    return geometry.corridor_proposals(group, track_width_nm=150000, clearance_nm=198000,
        board_bounds_nm=((0, 0), (40000000, 30000000)), deadline=time.monotonic()+5, **kwargs)


def test_pending_outer_lanes_keep_the_eligible_closed_intervening_neighbor():
    ports, targets = fixture()
    before = deepcopy((ports, targets))
    groups = geometry.group_corridors(ports[::-1], targets, eligible_nets=set(targets),
                                     pending_nets={'outer', 'other'})
    assert [[r['net'] for r in g] for g in groups] == [['outer', 'closed', 'other']]
    assert (ports, targets) == before
    assert all(r['target'] == tuple(targets[r['net']][0]['point']) and
               r['target_pad_id'] == targets[r['net']][0]['pad_id'] for r in groups[0])


def test_opposite_targets_faces_and_banks_split_in_physical_order():
    ports, targets = fixture()
    targets['closed'] = [dict(point=(-10000000, 3500000), layers=[2], pad_id='back')]
    groups = geometry.group_corridors(ports, targets, eligible_nets=set(targets))
    assert [[r['net'] for r in g] for g in groups] == [['outer'], ['closed'], ['other']]
    ports, targets = fixture()
    ports[1]['exit_layer'] = 0
    assert all(len(g) == 1 for g in geometry.group_corridors(ports, targets, eligible_nets=set(targets)))


def test_arbitrary_net_renaming_and_translation_preserve_grouping_and_lane_geometry():
    ports, targets = fixture()
    groups = geometry.group_corridors(ports, targets, eligible_nets=set(targets))
    other_ports, other_targets = fixture(('z9', 'a1', 'q7'), (50000000, 70000000))
    translated = geometry.group_corridors(other_ports, other_targets, eligible_nets=set(other_targets))
    assert [[r['source'] for r in g] for g in translated] == [
        [(r['source'][0]+50000000, r['source'][1]+70000000) for r in g] for g in groups]
    left = proposals(groups[0], max_candidates=8)
    right = geometry.corridor_proposals(translated[0], track_width_nm=150000, clearance_nm=198000,
        board_bounds_nm=((50000000, 70000000), (90000000, 100000000)),
        deadline=time.monotonic()+5, max_candidates=8)
    assert left and len(left) == len(right)
    assert [[[(x-50000000, y-70000000) for x, y in lane['path']] for lane in p['lanes']] for p in right] == [
        [lane['path'] for lane in p['lanes']] for p in left]


def test_same_face_proposals_have_owner_aware_capsules_covering_their_tracks():
    ports, targets = fixture()
    group = geometry.group_corridors(ports, targets, eligible_nets=set(targets))[0]
    results = proposals(group, max_candidates=12)
    assert results and len(results) <= 12
    for proposal in results:
        identities = [dict(net=n, source_key=[f's{i}']) for i, n in enumerate(('outer', 'closed', 'other'))]
        assert proposal['physical_order'] == identities
        assert proposal['diagnostics']['cohort_sources'] == identities
        assert proposal['diagnostics']['geometry_only'] is True
        records = proposal['envelope_records']
        assert records and len({r['id'] for r in records}) == len(records)
        for row, lane in zip(records, proposal['lanes']):
            assert row['layer'] == 2 and row['nets'] == [lane['net']]
            assert row['width_nm'] == 546000
        assert proposal['diagnostics']['cohort_members'] == ['outer', 'closed', 'other']
        for lane, source in zip(proposal['lanes'], group):
            assert lane['layer'] == 2 and valid_shape(lane['path'])
            assert lane['path'][0] == source['source'] and lane['path'][-1] == source['target']
            assert all(type(v) is int for p in lane['path'] for v in p)
            for a, b in zip(lane['path'], lane['path'][1:]):
                point = ((a[0]+b[0])/2, (a[1]+b[1])/2)
                assert any(_point_distance(point, u, v) <= (r['width_nm']-150000)/2
                           for r in records for u, v in zip(r['path'], r['path'][1:]))


def test_sparse_obstacle_rails_change_with_actual_obstacle_edges_and_use_normal_offsets():
    ports, targets = fixture()
    group = geometry.group_corridors(ports, targets, eligible_nets=set(targets))[0]
    first = proposals(group, obstacle_bounds=(((9000000, 6000000), (14000000, 10000000)),))
    second = proposals(group, obstacle_bounds=(((11000000, 8000000), (16000000, 12000000)),))
    rails = lambda rows: [p for p in rows if p['diagnostics']['family'] == 'rail']
    assert rails(first) and rails(second)
    assert {tuple(p['diagnostics']['spine']) for p in rails(first)} != {tuple(p['diagnostics']['spine']) for p in rails(second)}
    turned = [p for p in rails(first) if len(p['diagnostics']['spine']) > 2]
    assert turned
    for p in turned:
        bodies = p['diagnostics']['lane_bodies']
        for a, b in zip(bodies, bodies[1:]):
            # A horizontal segment and its following 45-degree segment have
            # the same normal gap (up to integer-nm rounding).
            gaps = []
            for (u, v), (x, y) in zip(zip(a, a[1:]), zip(b, b[1:])):
                dx, dy = v[0]-u[0], v[1]-u[1]
                gaps.append(abs(-dy*(x[0]-u[0])+dx*(x[1]-u[1]))/math.hypot(dx, dy))
            assert max(gaps)-min(gaps) < 2


def test_deadline_invalid_ownership_and_singleton_fallback_are_explicit():
    ports, targets = fixture()
    group = geometry.group_corridors(ports, targets, eligible_nets=set(targets))[0]
    assert geometry.corridor_proposals(group, track_width_nm=150000, clearance_nm=198000,
        board_bounds_nm=((0, 0), (40000000, 30000000)), deadline=time.monotonic()-1) == []
    for mutate in (lambda ps: ps[1].update(exit_nm=ps[0]['exit_nm']),
                   lambda ps: ps[1].update(pad_ids=ps[0]['pad_ids']),
                   lambda ps: ps[1].update(exit_nm=(1.5, 0)),
                   lambda ps: ps[1].update(heading_origin_nm=ps[1]['exit_nm'])):
        bad = deepcopy(ports)
        mutate(bad)
        with pytest.raises(ValueError):
            geometry.group_corridors(bad, targets, eligible_nets=set(targets))
    single = geometry.group_corridors(ports[:1], targets, eligible_nets={'outer'})[0]
    result = proposals(single)
    assert result and all(p['diagnostics']['singleton_fallback'] and not p['diagnostics']['bundle'] for p in result)
    assert all(len(p['lanes']) == 1 for p in result)


def test_reported_rail_bodies_are_present_in_the_actual_attached_lane():
    ports, targets = fixture()
    group = geometry.group_corridors(ports, targets, eligible_nets=set(targets))[0]
    for p in proposals(group, obstacle_bounds=(((9000000, 6000000), (14000000, 10000000)),)):
        if p['diagnostics']['family'] != 'rail':
            continue
        for lane, body in zip(p['lanes'], p['diagnostics']['lane_bodies']):
            for point in body:
                assert min(_point_distance(point, a, b) for a, b in zip(lane['path'], lane['path'][1:])) < 1


def test_inconsistent_repeated_target_pad_coordinates_are_rejected():
    ports, targets = fixture()
    targets['outer'].append(dict(point=(26000000, 9000000), layers=[0, 2], pad_id='t0'))
    with pytest.raises(ValueError):
        geometry.group_corridors(ports, targets, eligible_nets=set(targets))


def test_intervening_departure_heading_splits_the_whole_physical_bank():
    ports, targets = fixture()
    ports[1]['heading_origin_nm'] = (2000000, 2500000)
    groups = geometry.group_corridors(ports[::-1], targets, eligible_nets=set(targets))
    assert [[r['net'] for r in g] for g in groups] == [['outer'], ['closed'], ['other']]
    pending = geometry.group_corridors(ports, targets, eligible_nets=set(targets),
                                      pending_nets={'outer', 'other'})
    assert [[r['net'] for r in g] for g in pending] == [['outer'], ['other']]


@pytest.mark.parametrize('family', ['rail', 'existing'])
def test_even_cohort_half_nm_centroids_commute_with_one_nm_translation(family):
    ports, targets = fixture(('left', 'right'))
    # Odd pitch gives half-nm source/target means; absolute ties-to-even
    # rounding changes the relative proposal geometry under a 1 nm shift.
    for key in ('exit_nm', 'heading_origin_nm'):
        x, y = ports[1][key]
        ports[1][key] = (x, y+1)
    x, y = targets['right'][0]['point']
    targets['right'][0]['point'] = (x, y+1)
    before = deepcopy((ports, targets))
    original = geometry.group_corridors(ports, targets, eligible_nets=set(targets))[0]
    moved_ports, moved_targets = deepcopy((ports, targets))
    for row in moved_ports:
        for key in ('exit_nm', 'heading_origin_nm'):
            row[key] = tuple(v+1 for v in row[key])
    for rows in moved_targets.values():
        for row in rows:
            row['point'] = tuple(v+1 for v in row['point'])
    translated = geometry.group_corridors(moved_ports, moved_targets, eligible_nets=set(moved_targets))[0]
    options = dict(track_width_nm=150000, clearance_nm=198000, max_candidates=48)
    left = geometry.corridor_proposals(original, board_bounds_nm=((0, 0), (40000000, 30000000)),
        obstacle_bounds=(((9000000, 6000000), (14000000, 10000000)),), deadline=time.monotonic()+5, **options)
    right = geometry.corridor_proposals(translated, board_bounds_nm=((1, 1), (40000001, 30000001)),
        obstacle_bounds=(((9000001, 6000001), (14000001, 10000001)),), deadline=time.monotonic()+5, **options)

    def geometry_only(proposals, shift):
        def point(p):
            return tuple(v-shift for v in p)
        return [dict(lanes=[(r['net'], r['layer'], point(r['source']), point(r['target']),
                              [point(p) for p in r['path']]) for r in q['lanes']],
                     envelopes=[(r['layer'], r['nets'], r['width_nm'], [point(p) for p in r['path']])
                                for r in q['envelope_records']],
                     spine=[point(p) for p in q['diagnostics'].get('spine', [])],
                     bodies=[[point(p) for p in body] for body in q['diagnostics'].get('lane_bodies', [])])
                for q in proposals if q['diagnostics']['family'] == family]

    expected = geometry_only(left, 0)
    assert expected
    assert geometry_only(right, 1) == expected
    assert (ports, targets) == before


@pytest.mark.parametrize('family', ['rail', 'existing', 'singleton'])
def test_every_proposal_preserves_the_supplied_source_departure_heading(family):
    ports, targets = fixture(('left', 'right'))
    if family == 'singleton':
        ports = ports[:1]
        ports[0]['heading_origin_nm'] = (2000000, 2000000)
        targets['left'] = targets['left'][:1]
    group = geometry.group_corridors(ports, targets, eligible_nets={p['net'] for p in ports})[0]
    results = proposals(group, obstacle_bounds=(((9000000, 6000000), (14000000, 10000000)),))
    selected = [p for p in results if p['diagnostics']['family'] == family]
    assert selected
    for proposal in selected:
        for lane, row in zip(proposal['lanes'], group):
            a = lane['path'][0]
            z = next(p for p in lane['path'][1:] if p != a)
            leg = (z[0]-a[0], z[1]-a[1])
            heading = row['heading']
            dot = heading[0]*leg[0]+heading[1]*leg[1]
            assert dot > 0 and 2*dot*dot >= sum(v*v for v in heading)*sum(v*v for v in leg)


def test_attachment_selects_a_heading_compatible_octile_alternative_before_minimum():
    # Vertical-first reaches this diagonal body with one fewer bend, but is
    # incompatible with the existing eastward departure. The diagonal-first
    # octile attachment remains a valid 45-degree alternative.
    body = [(1000000, 3000000), (10000000, 12000000)]
    attached = geometry._attach(body, (0, 0), body[-1], 150000, heading=(1, 0))
    assert attached is not None
    path, span = attached
    assert path == [(0, 0), (1000000, 1000000), (1000000, 3000000), (10000000, 12000000)]
    assert span == body
    assert valid_shape(path)


def test_optional_native_departure_waypoint_survives_physical_grouping():
    ports, targets = fixture(('left', 'right'))
    ports[0]['departure_waypoint_nm'] = (2310001, 3000000)
    before = deepcopy(ports)
    group = geometry.group_corridors(ports, targets, eligible_nets=set(targets))[0]
    assert group[0]['departure_waypoint_nm'] == (2310001, 3000000)
    assert 'departure_waypoint_nm' not in group[1]
    assert ports == before


@pytest.mark.parametrize('family', ['rail', 'existing', 'singleton'])
def test_mandatory_departure_prefix_is_preserved_by_every_proposal_family(family):
    ports, targets = fixture(('left', 'right'))
    if family == 'singleton':
        ports = ports[:1]
    group = geometry.group_corridors(ports, targets, eligible_nets={p['net'] for p in ports})[0]
    for i, row in enumerate(group):
        row['departure_waypoint_nm'] = (row['source'][0]+310001+i*100003, row['source'][1])
    before = deepcopy(group)
    selected = [p for p in proposals(group, obstacle_bounds=(((9000000, 6000000), (14000000, 10000000)),))
                if p['diagnostics']['family'] == family]
    assert selected
    for proposal in selected:
        for lane, row in zip(proposal['lanes'], group):
            assert lane['source'] == row['source']
            assert lane['target'] == row['target'] and lane['target_pad_id'] == row['target_pad_id']
            assert lane['path'][:2] == [row['source'], row['departure_waypoint_nm']]
            tail = lane['path'][1:]
            a, b = tail[:2]
            leg = (b[0]-a[0], b[1]-a[1])
            dot = sum(row['heading'][i]*leg[i] for i in (0, 1))
            assert dot > 0 and 2*dot*dot >= sum(v*v for v in row['heading'])*sum(v*v for v in leg)
            assert valid_shape(lane['path'])
    assert group == before


def test_mandatory_departure_prefix_preserves_translation_and_arbitrary_renaming():
    def build(names, shift):
        ports, targets = fixture(names, shift)
        for i, port in enumerate(ports):
            port['departure_waypoint_nm'] = (port['exit_nm'][0]+310001+i*100003, port['exit_nm'][1])
        group = geometry.group_corridors(ports, targets, eligible_nets=set(targets))[0]
        return geometry.corridor_proposals(group, track_width_nm=150000, clearance_nm=198000,
            board_bounds_nm=((shift[0], shift[1]), (40000000+shift[0], 30000000+shift[1])),
            obstacle_bounds=(((9000000+shift[0], 6000000+shift[1]), (14000000+shift[0], 10000000+shift[1])),),
            deadline=time.monotonic()+5)
    original = build(('left', 'right'), (0, 0))
    renamed = build(('z9', 'a2'), (1, 1))
    assert original and len(original) == len(renamed)
    assert [[lane['path'] for lane in p['lanes']] for p in original] == [
        [[(x-1, y-1) for x, y in lane['path']] for lane in p['lanes']] for p in renamed]
    assert [p['diagnostics']['family'] for p in original] == [p['diagnostics']['family'] for p in renamed]


@pytest.mark.parametrize('waypoint', [(2000000, 3000000), (1900000, 3000000), (2300000, 3000001), (2300000.5, 3000000)])
def test_departure_waypoint_must_be_a_real_forward_point_on_the_heading(waypoint):
    ports, targets = fixture(('left', 'right'))
    ports[0]['departure_waypoint_nm'] = waypoint
    with pytest.raises(ValueError):
        geometry.group_corridors(ports, targets, eligible_nets=set(targets))


def via_proposals(via_width_nm, *, max_candidates=48, names=('left', 'right'), shift=(0, 0),
                  track_width_nm=200000, clearance_nm=173000, via_envelope_radius_nm=None):
    ports, targets = fixture(names, shift)
    for i, port in enumerate(ports):
        port['departure_waypoint_nm'] = (port['exit_nm'][0]+310001+i*100003, port['exit_nm'][1])
    group = geometry.group_corridors(ports, targets, eligible_nets=set(targets))[0]
    envelope = {} if via_envelope_radius_nm is None else dict(via_envelope_radius_nm=via_envelope_radius_nm)
    return geometry.corridor_proposals(group, track_width_nm=track_width_nm, clearance_nm=clearance_nm,
        via_width_nm=via_width_nm, max_candidates=max_candidates, **envelope,
        board_bounds_nm=((shift[0], shift[1]), (40000000+shift[0], 30000000+shift[1])),
        obstacle_bounds=(((9000000+shift[0], 6000000+shift[1]), (14000000+shift[0], 10000000+shift[1])),),
        deadline=time.monotonic()+5)


@pytest.mark.parametrize('track,gap,via,expected', [
    (200000, 173000, 500000, 548000),
    (250000, 173000, 500000, 573000),
    (200000, 200000, 500000, 575000),
    (200000, 173000, 700000, 648000),
])
def test_bridge_pitch_tracks_actual_via_track_and_clearance_dimensions(track, gap, via, expected):
    results = via_proposals(via, track_width_nm=track, clearance_nm=gap)
    assert results
    assert {p['diagnostics']['pitch_family'] for p in results} == {'compact', 'bridge'}
    assert {p['diagnostics']['pitch_nm'] for p in results} == {track+gap+25000, expected}
    assert all(p['diagnostics']['geometry_only'] for p in results)
    gaps = [gap for p in results if p['diagnostics']['family'] == 'rail' and
            p['diagnostics']['pitch_family'] == 'bridge'
            for run, gap in _parallel_intervals(*p['diagnostics']['lane_bodies']) if run > 0]
    assert gaps and all(abs(gap-expected) < 2 for gap in gaps)


@pytest.mark.parametrize('limit', [1, 2, 3, 7])
def test_compact_and_bridge_pitch_are_interleaved_per_spine_within_one_cap(limit):
    results = via_proposals(500000, max_candidates=limit)
    assert 0 < len(results) <= limit
    assert results[0]['diagnostics']['pitch_family'] == 'compact'
    if limit >= 2:
        assert [p['diagnostics']['pitch_family'] for p in results[:2]] == ['compact', 'bridge']
        assert results[0]['diagnostics']['spine'] == results[1]['diagnostics']['spine']
        assert [p['diagnostics']['pitch_nm'] for p in results[:2]] == [398000, 548000]
    for proposal in results:
        for lane in proposal['lanes']:
            assert lane['path'][:2] == [lane['source'], lane['departure_waypoint_nm']]
            assert valid_shape(lane['path'])


def test_optional_via_pitch_preserves_translation_and_arbitrary_renaming():
    original = via_proposals(500000)
    moved = via_proposals(500000, names=('z9', 'a2'), shift=(1, 1))
    assert original and len(original) == len(moved)
    assert [[lane['path'] for lane in p['lanes']] for p in original] == [
        [[(x-1, y-1) for x, y in lane['path']] for lane in p['lanes']] for p in moved]
    assert [(p['diagnostics']['family'], p['diagnostics']['pitch_family'], p['diagnostics']['pitch_nm']) for p in original] == [
        (p['diagnostics']['family'], p['diagnostics']['pitch_family'], p['diagnostics']['pitch_nm']) for p in moved]


def test_absent_via_metadata_retains_the_existing_compact_proposal_behavior():
    ports, targets = fixture(('left', 'right'))
    group = geometry.group_corridors(ports, targets, eligible_nets=set(targets))[0]
    assert proposals(group) == proposals(group, via_width_nm=None)


@pytest.mark.parametrize('via', [0, -1, True, 500000.5])
def test_optional_via_width_requires_a_positive_integer_dimension(via):
    with pytest.raises(ValueError):
        via_proposals(via)


@pytest.mark.parametrize('radius,expected', [(501000, 626000), (601001, 726001)])
def test_bridge_pitch_respects_actual_reserved_radius_without_widening_compact_or_lane_envelopes(radius, expected):
    baseline = via_proposals(500000)
    results = via_proposals(500000, via_envelope_radius_nm=radius)
    assert {p['diagnostics']['pitch_nm'] for p in baseline} == {398000, 548000}
    assert {p['diagnostics']['pitch_nm'] for p in results} == {398000, expected}
    assert [p['diagnostics']['pitch_family'] for p in results[:2]] == ['compact', 'bridge']
    assert all(record['width_nm'] == 546000 for p in results for record in p['envelope_records'])
    assert all(lane['path'][:2] == [lane['source'], lane['departure_waypoint_nm']]
               for p in results for lane in p['lanes'])


def test_reserved_via_radius_pitch_preserves_translation_and_renaming():
    original = via_proposals(500000, via_envelope_radius_nm=501001)
    translated = via_proposals(500000, via_envelope_radius_nm=501001, names=('z9', 'a2'), shift=(1, 1))
    assert original and len(original) == len(translated)
    assert [(p['diagnostics']['pitch_family'], p['diagnostics']['pitch_nm']) for p in original] == [
        (p['diagnostics']['pitch_family'], p['diagnostics']['pitch_nm']) for p in translated]
    assert [[lane['path'] for lane in p['lanes']] for p in original] == [
        [[(x-1, y-1) for x, y in lane['path']] for lane in p['lanes']] for p in translated]


@pytest.mark.parametrize('radius', [0, True, 501000.5])
def test_optional_reserved_via_radius_requires_a_positive_integer(radius):
    with pytest.raises(ValueError):
        via_proposals(500000, via_envelope_radius_nm=radius)


def same_net_source_case(net='shared', shift=(0, 0), different_headings=False):
    ports, old_targets = fixture(('left', 'right'), shift)
    ports[0]['pad_ids'] = ['s0b', 's0a']
    targets = {net: [old_targets['left'][0], dict(point=(2000000+shift[0], 20000000+shift[1]),
                                               layers=[0, 2], pad_id='north-target')]}
    for i, port in enumerate(ports):
        port['net'] = net
        if different_headings and i == 1:
            port['heading_origin_nm'] = (port['exit_nm'][0], port['exit_nm'][1]-1000000)
            port['departure_waypoint_nm'] = (port['exit_nm'][0], port['exit_nm'][1]+410004)
        else:
            port['departure_waypoint_nm'] = (port['exit_nm'][0]+310001+i*100003, port['exit_nm'][1])
    groups = geometry.group_corridors(ports[::-1], targets, eligible_nets={net}, pending_nets={net})
    candidates = [geometry.corridor_proposals(group, track_width_nm=200000, clearance_nm=173000,
        board_bounds_nm=((shift[0], shift[1]), (40000000+shift[0], 30000000+shift[1])),
        obstacle_bounds=(((9000000+shift[0], 6000000+shift[1]), (14000000+shift[0], 10000000+shift[1])),),
        deadline=time.monotonic()+5) for group in groups]
    return ports, targets, groups, candidates


@pytest.mark.parametrize('different_headings', [False, True])
def test_each_same_net_physical_root_survives_grouping_proposals_and_json_binding(different_headings):
    ports, targets, groups, candidates = same_net_source_case(different_headings=different_headings)
    assert [len(group) for group in groups] == ([1, 1] if different_headings else [2])
    assert sum(map(len, groups)) == 2
    assert sorted(tuple(row['source_key']) for group in groups for row in group) == [('s0a', 's0b'), ('s1',)]
    assert sorted(pad for group in groups for row in group for pad in row['pad_ids']) == ['s0a', 's0b', 's1']
    for group, proposals in zip(groups, candidates):
        assert proposals
        for proposal in proposals:
            identities = [dict(net='shared', source_key=row['source_key']) for row in group]
            assert proposal['physical_order'] == identities
            assert proposal['diagnostics']['cohort_sources'] == identities
            assert len(proposal['lanes']) == len(proposal['envelope_records']) == len(group)
            assert json.loads(json.dumps(proposal))['physical_order'] == identities
            for row, lane, envelope in zip(group, proposal['lanes'], proposal['envelope_records']):
                assert lane['source_key'] == row['source_key']
                assert lane['net'] == 'shared' and envelope['nets'] == ['shared']
                assert lane['source'] == row['source'] and lane['path'][:2] == [row['source'], row['departure_waypoint_nm']]
                assert lane['target_pad_id'] in {t['pad_id'] for t in targets['shared']}
                assert valid_shape(lane['path'])


@pytest.mark.parametrize('different_headings', [False, True])
def test_same_net_root_counts_and_geometry_survive_translation_and_electrical_renaming(different_headings):
    original = same_net_source_case(different_headings=different_headings)
    moved = same_net_source_case(net='arbitrary-owner', shift=(1, 1), different_headings=different_headings)
    assert [[r['source_key'] for r in group] for group in original[2]] == [[r['source_key'] for r in group] for group in moved[2]]
    assert [[len(p['lanes']) for p in proposals] for proposals in original[3]] == [[len(p['lanes']) for p in proposals] for proposals in moved[3]]
    assert [[[lane['path'] for lane in p['lanes']] for p in proposals] for proposals in original[3]] == [
        [[[(x-1, y-1) for x, y in lane['path']] for lane in p['lanes']] for p in proposals] for proposals in moved[3]]


def test_overlapping_physical_source_pads_are_rejected_even_for_one_electrical_owner():
    ports, old_targets = fixture(('left', 'right'))
    ports[0]['net'] = ports[1]['net'] = 'shared'
    ports[0]['pad_ids'] = ['s0', 'overlap']
    ports[1]['pad_ids'] = ['overlap', 's1']
    with pytest.raises(ValueError):
        geometry.group_corridors(ports, {'shared': old_targets['left']}, eligible_nets={'shared'})


def backward_singleton(turns=0, shift=(0, 0), name='terminal'):
    def point(value):
        x,y=value
        for _ in range(turns):x,y=-y,x
        return x+shift[0],y+shift[1]
    port=dict(net=name,exit_nm=point((60000000,80000000)),
              heading_origin_nm=point((60000000,79000000)),exit_layer=2,
              bank='physical-bank',pad_ids=['source-pad'],
              departure_waypoint_nm=point((60000000,82000000)))
    targets={name:[dict(point=point((10000000,60000000)),layers=[0],pad_id='field-pad')]}
    corners=[point(q) for q in ((0,0),(100000000,0),(0,90000000),(100000000,90000000))]
    bounds=(min(x for x,y in corners),min(y for x,y in corners),
            max(x for x,y in corners),max(y for x,y in corners))
    return geometry.group_corridors([port],targets,eligible_nets={name})[0],bounds


@pytest.mark.parametrize('turns',range(4))
def test_backward_singleton_with_mandatory_prefix_reuses_forward_rail_fallback(turns,monkeypatch):
    group,bounds=backward_singleton(turns)
    def inherited(*a,**kw):raise AssertionError('Inherited helper requires multiple lanes')
    monkeypatch.setattr(geometry,'corridor_candidates',inherited)
    results=geometry.corridor_proposals(group,track_width_nm=200000,clearance_nm=173000,
        board_bounds_nm=bounds,deadline=time.monotonic()+2,max_candidates=1)
    assert len(results)==1
    proposal=results[0];lane=proposal['lanes'][0];row=group[0]
    assert proposal['diagnostics']['family']=='rail' and proposal['diagnostics']['singleton_fallback']
    assert lane['path'][:2]==[row['source'],row['departure_waypoint_nm']]
    assert lane['path'][-1]==row['target'] and lane['target_pad_id']=='field-pad'
    assert row['target_layers']==[0] and lane['layer']==2
    assert geometry._departure_compatible(lane['path'],row['heading']) and valid_shape(lane['path'])
    assert geometry.paths_clear([lane['path']],[200000],0)
    assert all(bounds[0]<=x<=bounds[2] and bounds[1]<=y<=bounds[3] for x,y in lane['path'])


def test_backward_singleton_rail_fallback_preserves_translation_and_owner_renaming():
    def run(shift,name):
        group,bounds=backward_singleton(shift=shift,name=name)
        return geometry.corridor_proposals(group,track_width_nm=200000,clearance_nm=173000,
            board_bounds_nm=bounds,deadline=time.monotonic()+2,max_candidates=8)
    original=run((0,0),'old');moved=run((1,3),'renamed')
    assert original and len(original)==len(moved)
    assert [p['lanes'][0]['path'] for p in original]==[
        [(x-1,y-3) for x,y in p['lanes'][0]['path']] for p in moved]
    assert all(r['nets']==['renamed'] for p in moved for r in p['envelope_records'])


def return_cohort(turns=0,shift=(0,0),names=('a','b','c','d')):
    def point(value):
        x,y=value
        for _ in range(turns):x,y=-y,x
        return x+shift[0],y+shift[1]
    ports=[dict(net=name,exit_nm=point((60000000-i*500000,85000000)),
        heading_origin_nm=point((60000000-i*500000,84000000)),exit_layer=2,
        bank='physical-launch-bank',pad_ids=['root-'+str(i)],
        departure_waypoint_nm=point((60000000-i*500000,86000000))) for i,name in enumerate(names)]
    targets={name:[dict(point=point((10000000+i*10000000,75000000 if not i else 55000000)),
        layers=[0,2],pad_id='destination-'+str(i))] for i,name in enumerate(names)}
    corners=[point(q) for q in ((0,0),(100000000,0),(0,90000000),(100000000,90000000))]
    bounds=(min(x for x,y in corners),min(y for x,y in corners),max(x for x,y in corners),max(y for x,y in corners))
    return geometry.group_corridors(ports,targets,eligible_nets=set(names))[0],bounds


def return_proposals(turns=0,shift=(0,0),names=('a','b','c','d'),limit=48):
    group,bounds=return_cohort(turns,shift,names)
    return group,bounds,geometry.corridor_proposals(group,track_width_nm=150000,clearance_nm=173000,
        via_width_nm=500000,via_envelope_radius_nm=501000,board_bounds_nm=bounds,
        deadline=time.monotonic()+3,max_candidates=limit)


@pytest.mark.parametrize('turns',range(4))
def test_multilane_return_carrier_preserves_real_forward_prefixes_for_targets_behind(turns):
    group,bounds,results=return_proposals(turns)
    assert results
    if turns in (0,2):assert all(p['diagnostics']['family']=='return' for p in results)
    pitch_families={p['diagnostics']['pitch_family'] for p in results}
    assert pitch_families and pitch_families<={'compact','bridge'}
    if turns in (0,2):assert pitch_families=={'compact','bridge'}
    for proposal in results:
        assert proposal['diagnostics']['geometry_only'] and proposal['diagnostics']['bundle']
        for row,lane in zip(group,proposal['lanes']):
            assert lane['source_key']==row['source_key'] and lane['target_pad_id']==row['target_pad_id']
            assert lane['path'][:2]==[row['source'],row['departure_waypoint_nm']]
            assert lane['path'][-1]==row['target'] and lane['layer']==row['source_layer']
            assert valid_shape(lane['path']) and geometry._departure_compatible(lane['path'],row['heading'])
            assert geometry.paths_clear([lane['path']],[150000],0)
            assert all(bounds[0]<=x<=bounds[2] and bounds[1]<=y<=bounds[3] for x,y in lane['path'])
        if proposal['diagnostics']['family']!='return':continue
        intervals=[(run,gap) for a,b in zip(proposal['diagnostics']['lane_bodies'],proposal['diagnostics']['lane_bodies'][1:])
                   for run,gap in _parallel_intervals(a,b) if run>1000000 and gap<2*proposal['diagnostics']['pitch_nm']]
        assert intervals and all(abs(gap-proposal['diagnostics']['pitch_nm'])<2 for run,gap in intervals)


def test_return_carrier_translation_renaming_and_shared_candidate_cap():
    _,_,original=return_proposals()
    _,_,moved=return_proposals(shift=(1,3),names=('z9','q7','x4','b2'))
    assert original and len(original)==len(moved)
    assert [[lane['path'] for lane in p['lanes']] for p in original]==[
        [[(x-1,y-3) for x,y in lane['path']] for lane in p['lanes']] for p in moved]
    for limit in (1,2,3):
        _,_,limited=return_proposals(limit=limit)
        assert 0<len(limited)<=limit and limited==original[:limit]
