"""Editable generated-field ownership through real tiny native fallback."""
from copy import deepcopy
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from smilepcb.native_worker import p, snapshot
from smilepcb.keyboard_routing.conditional_geometry import FrozenProofRouter
from smilepcb.keyboard_routing import conditional_banks as api
from smilepcb.keyboard_routing.controller_ports import route_controller_ports
from smilepcb.keyboard_routing.field import build_field
from smilepcb.keyboard_routing.generation import generation_profile
from smilepcb.keyboard_routing.interface_launches import (
    current_launch_inventory, reserve_launches,
)
from smilepcb.keyboard_routing.native_graph import copper_graph
from native_collector_flow_fixture import fixture
from smilepcb.keyboard_process import apply_native_rules


out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)


def fresh_case():
    board, cfg = fixture(p.F_Cu)
    apply_native_rules(board, 'fr4-2l-1oz-0p5-0p2')
    before_ports = snapshot(board)
    ports = route_controller_ports(
        board, controller_refs={'U_MCU', 'C_RETURN'}, mcu_refs={'U_MCU'},
        plane_nets=cfg['plane_nets'], include_nets={'r0', 'r1', 'c0', 'c1'},
        access_mode='prefer_same_layer', continuation_nm=500000,
        clearance_nm=173000, deadline=time.monotonic() + 10)
    assert ports['accepted'], ports
    ownership = api.certify_generated_heads(
        board, cfg, before_ports, ports['ports'], deadline=time.monotonic() + 5)
    field = build_field(board, cfg, row_layer=p.F_Cu, column_layer=p.B_Cu,
                        deadline=time.monotonic() + 20)
    assert field['complete'], field
    entry = snapshot(board)
    return board, cfg, entry, ports, ownership, field


def split_one_field_track(board, field_ids):
    candidates = [t for t in board.GetTracks()
                  if t.m_Uuid.AsString() in field_ids and
                  isinstance(t, p.PCB_TRACK) and not isinstance(t, p.PCB_VIA) and
                  t.GetStart() != t.GetEnd()]
    # Prefer the longest axis-aligned segment. UUID order is random, and a
    # diagonal midpoint can round off the original line on an odd delta.
    straight = []
    for candidate in candidates:
        start, end = candidate.GetStart(), candidate.GetEnd()
        dx, dy = abs(end.x - start.x), abs(end.y - start.y)
        if dx == 0 or dy == 0:
            straight.append((max(dx, dy), candidate))
    if not straight:
        raise AssertionError('Fixture generated no splittable horizontal or vertical field segment')
    track = max(straight, key=lambda item: item[0])[1]
    old_id = track.m_Uuid.AsString()
    start, end = track.GetStart(), track.GetEnd()
    midpoint = p.VECTOR2I((start.x + end.x) // 2, (start.y + end.y) // 2)
    if midpoint == start or midpoint == end:
        raise AssertionError('Generated field segment is too short to split')
    original = {'start': [start.x, start.y], 'end': [end.x, end.y],
                'net': track.GetNetname(), 'layer': track.GetLayer(),
                'width': track.GetWidth()}
    board.Delete(track)
    created = []
    for first, last in ((start, midpoint), (midpoint, end)):
        segment = p.PCB_TRACK(board)
        segment.SetStart(first)
        segment.SetEnd(last)
        segment.SetWidth(original['width'])
        segment.SetLayer(original['layer'])
        segment.SetNet(board.FindNet(original['net']))
        board.Add(segment)
        created.append(segment.m_Uuid.AsString())
    assert old_id not in snapshot(board)['tracks']
    assert len(set(created)) == 2 and old_id not in created
    return old_id, created, original


def inject_fresh_orphan(board, net='r0'):
    # The fixture board and all of its copper fit within 40 mm. Place a fresh,
    # unlocked matrix segment well outside its edge so it forms an orphan-only
    # native component with no pad contact.
    orphan = p.PCB_TRACK(board)
    orphan.SetStart(p.VECTOR2I(50000000, 50000000))
    orphan.SetEnd(p.VECTOR2I(52000000, 50000000))
    orphan.SetWidth(150000)
    orphan.SetLayer(p.F_Cu)
    orphan.SetNet(board.FindNet(net))
    orphan.SetLocked(False)
    board.Add(orphan)
    uid = orphan.m_Uuid.AsString()
    shape = snapshot(board)['tracks'][uid]
    assert shape['net'] == net and shape['locked'] is False
    graph = copper_graph(board, {net}, time.monotonic() + 10)
    index = next(i for i, node in enumerate(graph['nodes'])
                 if node['item'].m_Uuid.AsString() == uid)
    component = [node['item'] for i, node in enumerate(graph['nodes'])
                 if graph['find'](i) == graph['find'](index)]
    assert all(not isinstance(item, p.PAD) for item in component)
    return uid


def run_case(label, *, fail_commit=False):
    board, cfg, entry, head_ports, ownership, field = fresh_case()
    model = deepcopy(cfg)
    field_ids = set(field['owned_ids'])
    field_owned = {u: entry['tracks'][u] for u in field_ids}
    assert len(field_ids) > 0 and field_ids <= entry['tracks'].keys()
    immutable_ids = set(entry['tracks']) - set(ownership.tracks) - field_ids
    raw_entry = FrozenProofRouter._physical_state(SimpleNamespace(board=board))
    calls = {'search': 0, 'route': 0, 'split': None, 'orphan_id': None,
             'orphan_absent_before_commit': None, 'commit_proof': 0,
             'commit_error': None, 'route_result': None}
    normal_search = api.conditional_search
    normal_route = api.route_free_feeders
    normal_commit_proof = api.prove_free_matrix

    def reject_order(order, attempt, **kwargs):
        calls['search'] += 1
        return {'accepted': False, 'reason': 'controlled_ordered_rejection',
                'attempt_records': []}

    def editable_route(target, target_cfg, retained, **kwargs):
        if calls['route']:
            raise AssertionError('Fallback router invoked more than once')
        calls['route'] += 1
        present = field_ids & set(retained['tracks'])
        (out / f'{label}-retained-field-check.json').write_text(json.dumps({
            'field_id_count': len(field_ids),
            'field_ids_in_retained': sorted(present),
        }, indent=2) + '\n')
        assert not present, 'Generated field IDs remain in the fallback retained hard frame'
        retired, created, original = split_one_field_track(target, field_ids)
        calls['split'] = {'retired': retired, 'created': created,
                          'original': original}
        report = normal_route(target, target_cfg, retained, **kwargs)
        calls['route_result'] = {
            'accepted': report.get('accepted'),
            'reason': report.get('reason'),
            'native_proof': (report.get('native_proof') or {}).get('accepted'),
            'reload_proof': (report.get('reload_proof') or {}).get('accepted'),
        }
        calls['orphan_id'] = inject_fresh_orphan(target)
        report['fixture_orphan_id'] = calls['orphan_id']
        report['fixture_split'] = deepcopy(calls['split'])
        return report

    def fail_after_caller_commit(target, *args, **kwargs):
        if target is board:
            calls['commit_proof'] += 1
            live_ids = set(snapshot(target)['tracks'])
            if calls['split'] and calls['split']['retired'] not in live_ids and set(calls['split']['created']) <= live_ids:
                calls['orphan_absent_before_commit'] = calls['orphan_id'] not in live_ids
                raise ValueError('controlled post-split caller commit proof rejection')
        return normal_commit_proof(target, *args, **kwargs)

    api.conditional_search = reject_order
    api.route_free_feeders = editable_route
    if fail_commit:
        api.prove_free_matrix = fail_after_caller_commit
    try:
        with reserve_launches(head_ports['ports']):
            scope = current_launch_inventory()
            scope_before = (deepcopy(scope.ports), set(scope.resolved),
                            set(scope.repair_reserved))
            if fail_commit:
                rejected = False
                try:
                    api.prepare_conditional_banks(
                        board, cfg, field, head_ports['ports'], ownership,
                        deadline=time.monotonic() + 70)
                except ValueError as error:
                    calls['commit_error'] = str(error)
                    rejected = calls['commit_error'] == \
                        'controlled post-split caller commit proof rejection'
                scope_exact = ((scope.ports, scope.resolved, scope.repair_reserved) ==
                               scope_before)
                rollback = {
                    'rejected': rejected,
                    'route_called': calls['route'] == 1,
                    'split_applied': calls['split'] is not None,
                    'caller_exact': snapshot(board) == entry,
                    'model_exact': cfg == model,
                    'raw_physical_exact':
                        FrozenProofRouter._physical_state(SimpleNamespace(board=board)) == raw_entry,
                    'scope_exact': scope_exact,
                    'commit_proof_calls': calls['commit_proof'],
                    'retired_field_id_restored':
                        calls['split']['retired'] in snapshot(board)['tracks'] if calls['split'] else False,
                    'field_exact': all(snapshot(board)['tracks'].get(u) == shape
                                       for u, shape in field_owned.items()),
                    'head_exact': all(snapshot(board)['tracks'].get(u) == shape
                                      for u, shape in ownership.tracks.items()),
                    'orphan_removed_before_caller_commit': calls['orphan_absent_before_commit'],
                    'orphan_absent_after_rollback': calls['orphan_id'] not in snapshot(board)['tracks'],
                }
                assert rollback == {
                    'rejected': True, 'route_called': True, 'split_applied': True,
                    'caller_exact': True, 'model_exact': True,
                    'raw_physical_exact': True, 'scope_exact': True,
                    'commit_proof_calls': 1, 'retired_field_id_restored': True,
                    'field_exact': True, 'head_exact': True,
                    'orphan_removed_before_caller_commit': True,
                    'orphan_absent_after_rollback': True,
                }, {**rollback, 'commit_error': calls['commit_error'],
                    'route_result': calls['route_result']}
                return {'rollback': rollback, 'field_ids': sorted(field_ids),
                        'split': calls['split'], 'model_unchanged': cfg == model,
                        'search_calls': calls['search']}

            result = api.prepare_conditional_banks(
                board, cfg, field, head_ports['ports'], ownership,
                deadline=time.monotonic() + 70)
            scope_exact = ((scope.ports, scope.resolved, scope.repair_reserved) ==
                           scope_before)
        fallback = result.get('free_feeders', {})
        report = fallback.get('routing', {})
        split = calls['split']
        orphan_ids = result.get('free_feeders', {}).get('retired_orphan_ids')
        assert orphan_ids == [calls['orphan_id']], result.get('free_feeders', {})
        assert calls['orphan_id'] not in snapshot(board)['tracks']
        assert result['accepted'] and result.get('source_mode') == 'native_pads', result
        assert fallback.get('accepted') and fallback.get('saved_reload_exact'), fallback
        assert result.get('retired_field_ids') == [split['retired']], result
        assert split['retired'] not in snapshot(board)['tracks']
        after = snapshot(board)
        assert all(after['tracks'].get(uid) == entry['tracks'][uid]
                   for uid in immutable_ids)
        assert cfg == model and scope_exact
        assert report.get('native_proof') is None or report['native_proof'].get('accepted')
        assert fallback['native_proof']['accepted'] and fallback['reload_proof']['accepted']
        caller_path = out / f'{label}-caller-commit.kicad_pcb'
        assert p.SaveBoard(str(caller_path), board)
        reloaded = p.LoadBoard(str(caller_path))
        apply_native_rules(reloaded, 'fr4-2l-1oz-0p5-0p2')
        assert snapshot(reloaded) == after
        assert FrozenProofRouter._physical_state(SimpleNamespace(board=reloaded)) == \
            FrozenProofRouter._physical_state(SimpleNamespace(board=board))
        delta = {u: q for u, q in after['tracks'].items()
                 if entry['tracks'].get(u) != q}
        assert set(split['created']) <= set(delta)
        assert all(delta[u]['net'] == split['original']['net'] and
                   delta[u]['layer'] == split['original']['layer'] and
                   delta[u]['width'] == split['original']['width'] for u in split['created'])
        return {
            'accepted': True,
            'source_mode': result['source_mode'],
            'saved_reload_exact': fallback['saved_reload_exact'],
            'native_proof': fallback['native_proof'],
            'reload_proof': fallback['reload_proof'],
            'caller_reload_exact': True,
            'immutable_copper_exact': True,
            'model_unchanged': cfg == model,
            'scope_unchanged': scope_exact,
            'retired_field_ids': result.get('retired_field_ids'),
            'retired_orphan_ids': orphan_ids,
            'orphan_removed_before_caller_commit': True,
            'split': split,
            'field_changes': fallback.get('field_changes'),
            'field_delta_ids': sorted(set(field_ids) - set(after['tracks']) |
                                      {u for u in field_ids if after['tracks'].get(u) != entry['tracks'][u]}),
            'entry_field_owned_count': len(field_ids),
            'entry_field_ids': sorted(field_ids),
            'mutable_track_count': len(delta),
            'immutable_track_count': len(immutable_ids),
            'search_calls': calls['search'],
        }
    finally:
        api.conditional_search = normal_search
        api.route_free_feeders = normal_route
        api.prove_free_matrix = normal_commit_proof


with generation_profile('dsn-process-v2'):
    report = {'accepted_case': run_case('accepted'),
              'rollback_case': run_case('rollback', fail_commit=True)}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'accepted': True, 'retired_field_ids':
                      report['accepted_case']['retired_field_ids'],
                      'rollback_exact': report['rollback_case']['rollback']['caller_exact']}))
