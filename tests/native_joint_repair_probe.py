#!/usr/bin/env python3
"""Native regression controls for public joint_repair; no authored repair paths.

This script creates fresh synthetic boards, calls keyboard_native.run, then
audits saved native graphs independently of the operation's reported booleans.
Run through test_native_joint_repair.py with a selected KiCad interpreter.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def case_spec(name):
    if name not in ('base', 'changed', 'protected'):
        raise ValueError('Unknown independent case')
    changed = name == 'changed'
    return {
        'case': name,
        'nets': {'target': 'pulse_link' if changed else 'signal_probe',
                 'field': 'stagger_lane' if changed else 'column_probe',
                 'guard': 'fixed_screen' if changed else 'barrier_probe',
                 'plane': 'return_sheet' if changed else 'plane_mesh',
                 'short_bus': 'maintenance_feed' if changed else 'auxiliary_supply'},
        'short_bus_translation_mm': [.5, 11.5] if changed else [0, 0],
        'shield_side': 'right' if changed else 'left',
        'protect_gate': name == 'protected',
        'expected_accepted': name != 'protected',
        'expected_bridge_half': 'left' if changed else 'right',
        'board_mm': [12, 20],
        'geometry_explanation': (
            'Coincident F/B vertical walls block ordinary target connectivity. '
            'Only the F central field access is movable. Wide opposite-edge F '
            'signal pads make its repaired connection a front-face crosscut. '
            'The B shield covers one full half of the bounded repair region, '
            'requiring a target or field back-face bridge through the other half. '
            'A protected branch reaches a third field pad; another protected '
            'spur becomes a padless retained fragment when the gate is removed.'),
    }


def graph_sets(board, nets, deadline, p, copper_graph):
    graph = copper_graph(board, set(nets), deadline, include_zones=False)
    groups = []
    pad_groups = []
    index = {}
    for root, indices in graph['groups'].items():
        group = frozenset(graph['nodes'][i]['item'].m_Uuid.AsString() for i in indices)
        pads = frozenset(graph['nodes'][i]['item'].m_Uuid.AsString() for i in indices
                         if isinstance(graph['nodes'][i]['item'], p.PAD)
                         and graph['nodes'][i]['item'].GetNumber())
        groups.append(group)
        if pads:
            pad_groups.append(pads)
        index.update((uid, root) for uid in group)
    return groups, pad_groups, index


def construct(folder, spec, api):
    p, snapshot, apply_native_rules, project_settings, capture, verify_native = api
    folder.mkdir(parents=True, exist_ok=False)
    project = folder / 'project'
    project.mkdir()
    board_path = project / 'keyboard.kicad_pcb'
    board = p.BOARD()
    board.SetCopperLayerCount(2)
    apply_native_rules(board, 'fr4-2l-1oz-0p5-0p2')
    board.SetFileName(str(board_path))
    for net in spec['nets'].values():
        board.Add(p.NETINFO_ITEM(board, net))
    vec = lambda x, y: p.VECTOR2I(round(x * 1000000), round(y * 1000000))
    for a, b in [((0, 0), (12, 0)), ((12, 0), (12, 20)),
                 ((12, 20), (0, 20)), ((0, 20), (0, 0))]:
        edge = p.PCB_SHAPE(board)
        edge.SetShape(p.SHAPE_T_SEGMENT)
        edge.SetStart(vec(*a))
        edge.SetEnd(vec(*b))
        edge.SetLayer(p.Edge_Cuts)
        edge.SetWidth(50000)
        board.Add(edge)
    parts = []
    pad_ids = {}
    created = []
    ids = {}
    roles = {}
    label_prefix = 'ALT_' if spec['case'] == 'changed' else 'FIX_'

    def pad(label, net, position, size, face):
        ref = label_prefix + label.upper()
        fp = p.FOOTPRINT(board)
        fp.SetReference(ref)
        fp.SetValue('Independent joint fixture')
        fp.SetFPID(p.LIB_ID('JointFixture', 'RectPad'))
        fp.SetLayer(face)
        fp.SetPosition(vec(*position))
        board.Add(fp)
        item = p.PAD(fp)
        item.SetNumber('1')
        item.SetNet(board.FindNet(net))
        item.SetAttribute(p.PAD_ATTRIB_SMD)
        item.SetShape(p.PAD_SHAPE_RECT)
        item.SetSize(vec(*size))
        layers = p.LSET()
        layers.AddLayer(face)
        item.SetLayerSet(layers)
        item.SetPosition(vec(*position))
        fp.Add(item)
        pad_ids[label] = item.m_Uuid.AsString()
        parts.append({'ref': ref, 'footprint': 'JointFixture:RectPad',
                      'nets': {'1': net}, 'region': 'test'})

    def track(label, net, a, b, face, role=None):
        item = p.PCB_TRACK(board)
        item.SetStart(vec(*a))
        item.SetEnd(vec(*b))
        item.SetWidth(board.GetDesignSettings().m_TrackMinWidth)
        item.SetLayer(face)
        item.SetNet(board.FindNet(net))
        board.Add(item)
        uid = item.m_Uuid.AsString()
        # Positive ownership is recorded at this creation, never inferred
        # from current copper minus a protected set.
        created.append({'uuid': uid, 'creator': 'fixture.track', 'label': label})
        ids[label] = uid
        if role:
            roles[uid] = role

    target, field, guard = (spec['nets'][k] for k in ('target', 'field', 'guard'))
    pad('signal_a', target, (2.2, 10), (2.8, .4), p.F_Cu)
    pad('signal_b', target, (9.8, 10), (2.8, .4), p.F_Cu)
    pad('field_top', field, (6, 2), (.4, .4), p.F_Cu)
    pad('field_bottom', field, (6, 18), (.4, .4), p.F_Cu)
    pad('field_branch', field, (4.4, 9), (.4, .4), p.F_Cu)
    shield_x = 3.4 if spec['shield_side'] == 'left' else 8.6
    pad('shield', guard, (shield_x, 10), (5.2, 8.8), p.B_Cu)
    track('top_trunk', field, (6, .8), (6, 8), p.F_Cu, 'column_trunk')
    track('gate', field, (6, 8), (6, 12), p.F_Cu, 'column_access')
    track('bottom_trunk', field, (6, 12), (6, 19.2), p.F_Cu, 'column_trunk')
    track('pad_branch', field, (6, 9), (4.4, 9), p.F_Cu, 'column_access')
    track('padless_spur', field, (6, 11), (7.6, 11), p.F_Cu, 'column_access')
    track('back_wall', guard, (6, .8), (6, 19.2), p.B_Cu)
    # This positively generated access ends inside a future plane, not at
    # current copper. Pre-zone tail pruning must not erase its fill contact.
    plane = spec['nets']['plane']
    pad('plane_a', plane, (10, 2), (.4, .4), p.F_Cu)
    pad('plane_b', plane, (10, 6), (.4, .4), p.F_Cu)
    track('future_plane_stub', plane, (10, 2), (10, 4), p.F_Cu)
    # A separate complete net exercises covered short-chain pruning followed
    # by finite-bus endpoint normalization. All coordinates are fixture-local.
    tail_net = spec['nets']['short_bus']
    dx, dy = spec['short_bus_translation_mm']
    shifted = lambda x, y: (x + dx, y + dy)
    pad('tail_leaf', tail_net, shifted(4.7, 4.5), (.4, .4), p.F_Cu)
    pad('tail_required', tail_net, shifted(1.72, 3.6), (.14, .14), p.F_Cu)
    track('tail_bus', tail_net, shifted(2, 3), shifted(2, 5.5), p.F_Cu)
    track('tail_branch', tail_net, shifted(4.7, 4.5), shifted(2.08, 4.5), p.F_Cu)
    track('tail_diagonal', tail_net, shifted(2.08, 4.5), shifted(2, 4.42), p.F_Cu)
    track('tail_stub', tail_net, shifted(2, 4.42), shifted(2, 4.36), p.F_Cu)
    track('tail_required_t', tail_net, shifted(1.72, 3.6), shifted(2, 3.6), p.F_Cu)
    write_json(project / 'keyboard.kicad_pro', project_settings('fr4-2l-1oz-0p5-0p2'))
    cfg = {'schema': 1, 'parts': parts, 'plane_nets': [plane],
           'layout': {'keys': [], 'rows': 0, 'cols': 0, 'rgb': False},
           'routing_preparation': {'mode': 'legacy-via'}}
    write_json(project / '.smilepcb-keyboard.json', cfg)
    if not p.SaveBoard(str(board_path), board):
        raise ValueError('Cannot save independent fixture')
    board = p.LoadBoard(str(board_path))
    # Saved rules are authoritative: synthetic boards can reload with a
    # larger minimum than the initial in-memory process setup. Raise only
    # explicitly created tracks; never lower or rewrite project/native rules.
    rule_names = ('m_MinClearance', 'm_TrackMinWidth', 'm_CopperEdgeClearance',
                  'm_MinThroughDrill', 'm_ViasMinSize', 'm_ViasMinAnnularWidth',
                  'm_HoleClearance')
    effective_rules = lambda current: {
        name: int(getattr(current.GetDesignSettings(), name)) for name in rule_names}
    loaded_rules = effective_rules(board)
    effective_minimum = loaded_rules['m_TrackMinWidth']
    if effective_minimum <= 0:
        raise ValueError('Saved fixture has no positive native track minimum')
    loaded_before = snapshot(board)
    created_ids = {q['uuid'] for q in created}
    if set(loaded_before['tracks']) != created_ids:
        raise ValueError('Saved fixture differs from its positive creation inventory')
    expected = json.loads(json.dumps(loaded_before))
    width_changes = []
    for item in board.GetTracks():
        uid = item.m_Uuid.AsString()
        if uid not in created_ids or item.GetClass() != 'PCB_TRACK':
            raise ValueError('Width normalization requires explicit created tracks')
        old_width = int(item.GetWidth())
        new_width = max(old_width, effective_minimum)
        if new_width != old_width:
            item.SetWidth(new_width)
            width_changes.append({'uuid': uid, 'before_nm': old_width, 'after_nm': new_width})
        expected['tracks'][uid]['width'] = new_width
    project_rules_path = project / 'keyboard.kicad_pro'
    project_rules_bytes = project_rules_path.read_bytes()
    if not p.SaveBoard(str(board_path), board):
        raise ValueError('Cannot save native-minimum fixture widths')
    if project_rules_path.read_bytes() != project_rules_bytes:
        raise ValueError('Fixture width adjustment changed project rule bytes')
    board = p.LoadBoard(str(board_path))
    saved_rules = effective_rules(board)
    if saved_rules != loaded_rules:
        raise ValueError('Fixture width adjustment changed effective native rules')
    before = snapshot(board)
    if before != expected:
        raise ValueError('Fixture width adjustment changed more than declared track widths')
    if any(q['width'] < saved_rules['m_TrackMinWidth'] for q in before['tracks'].values()):
        raise ValueError('Saved fixture width remains below actual native minimum')
    width_normalization = {
        'policy': 'raise_explicit_created_tracks_to_saved_native_minimum',
        'loaded_rules_before_nm': loaded_rules, 'saved_rules_after_nm': saved_rules,
        'effective_minimum_nm': effective_minimum,
        'project_rules_sha256': digest(project_rules_path),
        'project_rule_bytes_unchanged': True, 'native_rules_unchanged': True,
        'width_changes': width_changes,
        'saved_track_widths_nm': {u: q['width'] for u, q in before['tracks'].items()},
    }
    terminals = {pad_ids[label]: ['columns', field]
                 for label in ('field_top', 'field_bottom', 'field_branch')}
    report = {'complete': True, 'roles': roles, 'owned_ids': sorted(roles),
              'terminals': terminals, 'scope': 'matrix-with-controller'}
    contract = capture(before, report)
    verify_native(board, contract, deadline=time.monotonic() + 8)
    write_json(project / '.smilepcb-field.json', contract)
    protected = [ids[k] for k in ('back_wall', 'pad_branch', 'padless_spur', 'tail_bus')]
    if spec['protect_gate']:
        protected.append(ids['gate'])
    metadata = {'spec': spec, 'track_ids': ids, 'pad_ids': pad_ids,
                'generated_ids': sorted(q['uuid'] for q in created),
                'creation_events': created, 'protected_ids': sorted(protected),
                'future_plane': {'net': plane, 'stub_id': ids['future_plane_stub'],
                                 'pad_ids': [pad_ids['plane_a'], pad_ids['plane_b']],
                                 'layer': int(p.F_Cu),
                                 'outline_nm': [[9400000, 3700000], [10600000, 3700000],
                                                [10600000, 6400000], [9400000, 6400000]]},
                'short_bus': {'net': tail_net, 'bus_id': ids['tail_bus'],
                              'branch_id': ids['tail_branch'], 'stub_id': ids['tail_stub'],
                              'diagonal_id': ids['tail_diagonal'],
                              'required_t_id': ids['tail_required_t'],
                              'pad_ids': [pad_ids['tail_leaf'], pad_ids['tail_required']],
                              'translation_nm': [round(dx * 1000000), round(dy * 1000000)],
                              'expected_branch_end_nm': [round((2 + dx) * 1000000), round((4.5 + dy) * 1000000)]},
                'fixture_width_normalization': width_normalization,
                'input_geometry_nm': {
                    'board_bounds': [0, 0, 12000000, 20000000],
                    'barrier_y_limits': [800000, 19200000],
                    'signal_pad_centres': [[2200000, 10000000], [9800000, 10000000]],
                    'signal_pad_size': [2800000, 400000],
                    'signal_pad_outer_edge_inset': 800000,
                    'shield_center': [round(shield_x * 1000000), 10000000],
                    'shield_size': [5200000, 8800000],
                    'shield_outer_edge_inset': 800000,
                    'central_gate_y_limits': [8000000, 12000000],
                },
                'snapshot': before, 'board_sha256': digest(board_path)}
    write_json(folder / 'fixture.json', metadata)
    shutil.copytree(project, folder / 'before-project')
    return project, metadata


def audit_case(project, metadata, result, before_groups, before_pads, api):
    p, snapshot, copper_graph, verify_native, load_field, FrozenProofRouter, via_clear = api
    path = project / 'keyboard.kicad_pcb'
    board = p.LoadBoard(str(path))
    after = snapshot(board)
    before = metadata['snapshot']
    spec = metadata['spec']
    generated = set(metadata['generated_ids'])
    removed = set(before['tracks']) - set(after['tracks'])
    added = set(after['tracks']) - set(before['tracks'])
    groups, pads, index = graph_sets(board, spec['nets'].values(), time.monotonic() + 8, p, copper_graph)
    target = {metadata['pad_ids'][k] for k in ('signal_a', 'signal_b')}
    target_before = [q for q in before_groups if q & target]
    if len(target_before) != 2:
        raise ValueError('Fixture did not start with two target components')
    expected = {frozenset(q - removed) for q in before_groups if q - removed and not q & target}
    expected.add(frozenset().union(*target_before) - removed)
    old_members = frozenset().union(*expected)
    observed = {frozenset(q & old_members) for q in groups if q & old_members}
    target_pad_before = {q for q in before_pads if q & target}
    expected_pads = (set(before_pads) - target_pad_before) | {frozenset().union(*target_pad_before)}
    field_ids = [metadata['pad_ids'][k] for k in ('field_top', 'field_bottom', 'field_branch')]
    branch_ids = [metadata['track_ids'][k] for k in ('pad_branch', 'padless_spur')]
    proofs = {
        'joint_removal_required_and_observed': bool(removed) and metadata['track_ids']['gate'] in removed,
        'removed_only_explicit_generated': removed <= generated,
        'protected_copper_exact': all(after['tracks'].get(u) == before['tracks'][u] for u in metadata['protected_ids']),
        'retained_copper_exact': all(after['tracks'].get(u) == q for u, q in before['tracks'].items() if u not in removed),
        'footprints_and_pads_exact': after['footprints'] == before['footprints'],
        'every_retained_component_exact': observed == expected,
        'every_other_pad_partition_exact': set(pads) == expected_pads,
        'target_saved_connected': len({index[u] for u in target}) == 1,
        'real_branch_and_padless_spur_rejoined': len({index[u] for u in field_ids + branch_ids}) == 1,
        'new_copper_attached': bool(added) and all(q & old_members for q in groups if q & added),
    }
    verify_native(board, load_field(project), deadline=time.monotonic() + 8)
    graph = copper_graph(board, set(spec['nets'].values()), time.monotonic() + 8, include_zones=False)
    proof = FrozenProofRouter(board, deadline=time.monotonic() + 8, track_width_nm=board.GetDesignSettings().m_TrackMinWidth,
                              graph=graph, covered_nets=set(spec['nets'].values()))
    actual = {q.m_Uuid.AsString(): q for q in board.GetTracks()}
    clearance = True
    for uid in sorted(added):
        item = actual[uid]
        legal = via_clear(proof, item) if isinstance(item, p.PCB_VIA) else proof.clear(
            p.SHAPE_SEGMENT(item.GetStart(), item.GetEnd(), item.GetWidth()), item.GetLayer(), item.GetNetname())
        clearance = clearance and bool(legal)
    proof.assert_unchanged()
    proofs['independent_saved_full_union_clearance'] = clearance
    participating_nets = {spec['nets']['target'], spec['nets']['field']}
    participating_back = [q for u, q in after['tracks'].items()
                          if u in added and q['net'] in participating_nets
                          and q['class'] == 'PCB_TRACK' and q['layer'] == p.B_Cu]
    proofs['participating_net_has_back_bridge'] = bool(participating_back)
    side = spec['expected_bridge_half']
    proofs['bridge_uses_required_unshielded_half'] = bool(participating_back) and all(
        min(q['start'][0], q['end'][0]) > 6000000 if side == 'right'
        else max(q['start'][0], q['end'][0]) < 6000000 for q in participating_back)
    inverse_nets = {v: k for k, v in spec['nets'].items()}
    geometry = sorted(json.dumps({**q, 'net': inverse_nets[q['net']]}, sort_keys=True)
                      for uid, q in after['tracks'].items() if uid in added and q['net'] in participating_nets)
    return {'checks': proofs, 'passed': all(proofs.values()), 'added_ids': sorted(added),
            'removed_ids': sorted(removed), 'normalized_new_geometry': geometry,
            'saved_snapshot': after, 'board_sha256': digest(path),
            'field_receipt_sha256': digest(project / '.smilepcb-field.json')}



def audit_future_plane(folder, metadata, api):
    """Fill independent copies; never add a zone to the product input/output.

    A native control with only the original access removed proves the future
    plane cannot reach pad A without that access. Missing product copper is a
    failed check in the report, not an exception that hides the saved result.
    """
    p, snapshot, copper_graph = api
    plane = metadata['future_plane']
    stub = plane['stub_id']
    pads = set(plane['pad_ids'])
    projects = {name: folder / name for name in ('before-project', 'project')}
    tree_hashes = lambda path: {str(q.relative_to(path)): digest(q)
                              for q in path.rglob('*') if q.is_file()}
    original_hashes = {name: tree_hashes(path) for name, path in projects.items()}
    field = json.loads((projects['before-project'] / '.smilepcb-field.json').read_text())
    cfg = json.loads((projects['before-project'] / '.smilepcb-keyboard.json').read_text())
    audit_root = folder / 'future-plane-audit'
    audit_root.mkdir()
    worlds = []
    observations = {}
    for label, source_name, remove_stub in (
            ('before', 'before-project', False),
            ('without_stub', 'before-project', True),
            ('after', 'project', False)):
        project = audit_root / label
        shutil.copytree(projects[source_name], project)
        board_path = project / 'keyboard.kicad_pcb'
        board = p.LoadBoard(str(board_path))
        before = snapshot(board)
        no_input_planes = not any(not z.GetIsRuleArea() for z in board.Zones())
        stub_exact = before['tracks'].get(stub) == metadata['snapshot']['tracks'][stub]
        if remove_stub:
            item = next((q for q in board.GetTracks() if q.m_Uuid.AsString() == stub), None)
            if item is None:
                raise ValueError('Future-plane negative control lost its original access')
            board.Remove(item)
            worlds.append(item)
        entry = snapshot(board)
        _, _, pre_index = graph_sets(board, {plane['net']}, time.monotonic() + 8, p, copper_graph)
        pre_connected = pads <= pre_index.keys() and len({pre_index[u] for u in pads}) == 1
        zone = p.ZONE(board)
        zone.SetLayer(plane['layer'])
        zone.SetNet(board.FindNet(plane['net']))
        zone.SetLocalClearance(200000)
        zone.SetMinThickness(200000)
        zone.SetPadConnection(p.ZONE_CONNECTION_THT_THERMAL)
        zone.SetThermalReliefGap(250000)
        zone.SetThermalReliefSpokeWidth(300000)
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in plane['outline_nm']:
            outline.Append(x, y)
        board.Add(zone)
        filled = bool(p.ZONE_FILLER(board).Fill(board.Zones()))
        if not filled:
            raise ValueError('Future-plane audit native fill failed')
        if snapshot(board) != entry:
            raise ValueError('Future-plane audit fill changed fixed copper or pads')
        if not p.SaveBoard(str(board_path), board):
            raise ValueError('Cannot save independent future-plane audit')
        saved = p.LoadBoard(str(board_path))
        graph = copper_graph(saved, {plane['net']}, time.monotonic() + 8)
        index = {node['item'].m_Uuid.AsString(): graph['find'](i)
                 for i, node in enumerate(graph['nodes']) if isinstance(node['item'], p.PAD)}
        connected = pads <= index.keys() and len({index[u] for u in pads}) == 1
        zones = [z for z in saved.Zones() if not z.GetIsRuleArea()]
        observations[label] = {
            'input_had_no_planes': no_input_planes,
            'input_stub_exact': stub_exact,
            'before_fill_pads_connected': pre_connected,
            'saved_filled_pads_connected': connected,
            'saved_fixed_copper_exact': snapshot(saved) == entry,
            'saved_zone_complete': len(zones) == 1 and all(z.IsFilled() and not z.NeedRefill() for z in zones),
            'board_sha256': digest(board_path),
        }
        worlds.append((board, zone, saved, graph, zones))
    checks = {
        'renamed_configured_plane_net': plane['net'] in cfg['plane_nets'] and plane['net'] != 'GND',
        'stub_has_positive_generated_ownership': stub in metadata['generated_ids'],
        'stub_not_otherwise_protected': (stub not in metadata['protected_ids']
            and stub not in field['roles'] and stub not in field.get('prerequisite_tracks', {})
            and not metadata['snapshot']['tracks'][stub]['locked']),
        'product_input_and_output_had_no_planes': all(q['input_had_no_planes'] for q in observations.values()),
        'plane_pads_separate_before_fill': all(not q['before_fill_pads_connected'] for q in observations.values()),
        'original_future_fill_connects': observations['before']['saved_filled_pads_connected'],
        'removing_only_stub_breaks_future_fill': not observations['without_stub']['saved_filled_pads_connected'],
        'product_saved_stub_exact': observations['after']['input_stub_exact'],
        'product_future_fill_connects': observations['after']['saved_filled_pads_connected'],
        'audit_fill_save_preserves_fixed_copper': all(q['saved_fixed_copper_exact'] for q in observations.values()),
        'all_audit_planes_complete': all(q['saved_zone_complete'] for q in observations.values()),
        'product_and_original_files_unchanged': all(tree_hashes(path) == original_hashes[name]
            for name, path in projects.items()),
    }
    report = {'passed': all(checks.values()), 'checks': checks, 'plane_net': plane['net'],
              'stub_id': stub, 'observations': observations}
    write_json(audit_root / 'result.json', report)
    return report


def audit_short_bus_tail(folder, metadata, saved_audit, api):
    """Observe the cascade from saved native geometry and independent graphs.

    Counterfactual deletions occur only on separate unsaved board loads. A
    missing expected edit produces a failed report rather than an audit crash.
    """
    p, snapshot, copper_graph = api
    tail = metadata['short_bus']
    paths = {name: folder / name for name in ('before-project', 'project')}
    tree_hashes = lambda path: {str(q.relative_to(path)): digest(q)
                              for q in path.rglob('*') if q.is_file()}
    original_hashes = {name: tree_hashes(path) for name, path in paths.items()}
    before_board = p.LoadBoard(str(paths['before-project'] / 'keyboard.kicad_pcb'))
    after_board = p.LoadBoard(str(paths['project'] / 'keyboard.kicad_pcb'))
    before, after = snapshot(before_board), snapshot(after_board)
    pad_ids = set(tail['pad_ids'])
    ids = {key: tail[key] for key in ('bus_id', 'branch_id', 'stub_id', 'diagonal_id', 'required_t_id')}
    editable = {ids[key] for key in ('branch_id', 'stub_id', 'diagonal_id', 'required_t_id')}
    field = json.loads((paths['before-project'] / '.smilepcb-field.json').read_text())
    cfg = json.loads((paths['before-project'] / '.smilepcb-keyboard.json').read_text())
    all_items = [*before_board.GetTracks(), *(d for f in before_board.GetFootprints() for d in f.Pads())]
    native = {q.m_Uuid.AsString(): q for q in all_items}
    covered_ends = {}
    for uid in sorted(editable):
        item = native[uid]
        face = item.GetLayer()
        uncovered = []
        for label, point in (('start', item.GetStart()), ('end', item.GetEnd())):
            probe = p.SHAPE_CIRCLE(point, 0)
            contacts = []
            for other in all_items:
                if other.m_Uuid.AsString() == uid or other.GetNetname() != tail['net'] or not other.IsOnLayer(face):
                    continue
                shape = p.SHAPE_SEGMENT(other.GetStart(), other.GetEnd(), 0) if other.GetClass() == 'PCB_TRACK' else other.GetEffectiveShape(face)
                if p.SHAPE.Collide(probe, shape, 0):
                    contacts.append(other.m_Uuid.AsString())
            if not contacts:
                uncovered.append(label)
        covered_ends[uid] = uncovered
    def connected(board):
        _, _, index = graph_sets(board, {tail['net']}, time.monotonic() + 8, p, copper_graph)
        return pad_ids <= index.keys() and len({index[u] for u in pad_ids}) == 1
    counterfactual = {}
    worlds = []
    for label in ('required_t_id', 'branch_id'):
        board = p.LoadBoard(str(paths['before-project'] / 'keyboard.kicad_pcb'))
        item = next(q for q in board.GetTracks() if q.m_Uuid.AsString() == ids[label])
        board.Remove(item)
        counterfactual[label] = connected(board)
        worlds.append((board, item))
    old = before['tracks']
    new = after['tracks']
    removed, added = set(old) - set(new), set(new) - set(old)
    expected_branch = {**old[ids['branch_id']], 'end': tail['expected_branch_end_nm']}
    matches = [uid for uid in added if new[uid] == expected_branch]
    expected_removed = {ids[k] for k in ('stub_id', 'diagonal_id', 'branch_id')}
    tail_removed = {uid for uid in removed if old[uid]['net'] == tail['net']}
    tail_added = {uid for uid in added if new[uid]['net'] == tail['net']}
    checks = {
        'separate_non_plane_non_field_net': tail['net'] not in cfg['plane_nets'] and tail['net'] != metadata['spec']['nets']['field'],
        'editable_chain_has_positive_unprotected_ownership': editable <= set(metadata['generated_ids']) and not editable & set(metadata['protected_ids'])
            and not editable & set(field['roles']) and not editable & set(field.get('prerequisite_tracks', {}))
            and all(not old[u]['locked'] for u in editable),
        'all_initial_chain_endpoints_covered': all(not q for q in covered_ends.values()),
        'original_two_pads_connected': connected(before_board),
        'removing_required_short_t_breaks_connection': not counterfactual['required_t_id'],
        'removing_needed_branch_breaks_connection': not counterfactual['branch_id'],
        'protected_bus_exact': ids['bus_id'] in metadata['protected_ids'] and new.get(ids['bus_id']) == old[ids['bus_id']],
        'required_short_t_exact': new.get(ids['required_t_id']) == old[ids['required_t_id']],
        'covered_stub_and_diagonal_removed': {ids['stub_id'], ids['diagonal_id']} <= removed,
        'needed_branch_has_exact_finite_bus_endpoint': ids['branch_id'] in removed and len(matches) == 1,
        'only_expected_tail_delta': tail_removed == expected_removed and tail_added == set(matches) and len(matches) == 1,
        'saved_two_pads_connected': connected(after_board),
        'saved_footprints_and_pads_exact': after['footprints'] == before['footprints'],
        'saved_full_conductor_and_pad_partitions_preserved': saved_audit['checks']['every_retained_component_exact']
            and saved_audit['checks']['every_other_pad_partition_exact'],
        'saved_full_union_clearance': saved_audit['checks']['independent_saved_full_union_clearance'],
        'product_and_original_files_unchanged': all(tree_hashes(path) == original_hashes[name] for name, path in paths.items()),
    }
    report = {'passed': all(checks.values()), 'checks': checks, 'net': tail['net'],
              'translation_nm': tail['translation_nm'], 'initial_uncovered_ends': covered_ends,
              'counterfactual_pads_connected': counterfactual, 'replacement_ids': matches,
              'tail_removed_ids': sorted(tail_removed), 'tail_added_ids': sorted(tail_added)}
    write_json(folder / 'short-bus-audit.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--case', choices=['base', 'changed', 'protected', 'all'], default='all')
    parser.add_argument('--budget-s', type=float, default=82)
    args = parser.parse_args()
    if not 0 < args.budget_s <= 82:
        parser.error('--budget-s must be in (0,82]')
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(args.source.resolve()))
    from smilepcb.native_worker import p, snapshot
    from smilepcb.keyboard_native import run
    from smilepcb.keyboard_process import apply_native_rules, project_settings
    from smilepcb.keyboard_routing.generation import generation_profile
    from smilepcb.routing_quality import using_quality_profile
    from smilepcb.keyboard_routing.field_contract import capture, verify_native, load
    from smilepcb.keyboard_routing.native_graph import copper_graph
    from smilepcb.keyboard_routing.conditional_geometry import FrozenProofRouter, saved_via_clear_cached
    chosen = ('base', 'changed', 'protected') if args.case == 'all' else (args.case,)
    report = {'passed': False, 'public_entry': 'smilepcb.keyboard_native.run', 'cases': {},
              'builder_sha256': digest(__file__), 'source': str(args.source.resolve())}
    for name in chosen:
        row = {'passed': False, 'product_operation_called': False}
        report['cases'][name] = row
        folder = args.output / name
        started = time.monotonic()
        try:
            with generation_profile('dsn-process-v2'), using_quality_profile('turns'):
                project, metadata = construct(folder, case_spec(name),
                    (p, snapshot, apply_native_rules, project_settings, capture, verify_native))
                board_path = project / 'keyboard.kicad_pcb'
                field_path = project / '.smilepcb-field.json'
                original = {board_path: board_path.read_bytes(), field_path: field_path.read_bytes()}
                board = p.LoadBoard(str(board_path))
                pregraph = copper_graph(board, set(metadata['spec']['nets'].values()), time.monotonic()+8, include_zones=False)
                preproof = FrozenProofRouter(board, deadline=time.monotonic()+8,
                    track_width_nm=board.GetDesignSettings().m_TrackMinWidth,
                    graph=pregraph, covered_nets=set(metadata['spec']['nets'].values()))
                details = {
                    'stage': 'fixture-native-preflight', 'product_called': False,
                    'board_sha256': digest(board_path), 'passed': False,
                    'saved_rules_nm': metadata['fixture_width_normalization']['saved_rules_after_nm'],
                    'oracle_edge_clearance_nm': int(preproof.edge_clearance),
                    'oracle_minimum_clearance_nm': int(preproof.minimum_clearance),
                    'input_geometry_nm': metadata['input_geometry_nm'],
                    'objects': [], 'failures': [],
                }
                row['fixture_native_preflight_details'] = details
                for track in board.GetTracks():
                    shape = p.SHAPE_SEGMENT(track.GetStart(), track.GetEnd(), track.GetWidth())
                    hits = sorted(set(preproof.collisions(shape, track.GetLayer(), track.GetNetname())))
                    item = {'uuid': track.m_Uuid.AsString(), 'kind': track.GetClass(),
                            'net': track.GetNetname(), 'layer': int(track.GetLayer()),
                            'layer_name': board.GetLayerName(track.GetLayer()),
                            'start_nm': [track.GetStart().x, track.GetStart().y],
                            'end_nm': [track.GetEnd().x, track.GetEnd().y],
                            'width_nm': int(track.GetWidth()), 'collision_ids': hits,
                            'width_below_native_minimum': track.GetWidth() < board.GetDesignSettings().m_TrackMinWidth}
                    details['objects'].append(item)
                    if hits or item['width_below_native_minimum']:
                        details['failures'].append(item)
                # All fixture pads are immutable native rectangles. Query
                # their actual copper shapes on each face, including exact
                # board-edge and foreign-copper collisions, before product.
                for footprint in board.GetFootprints():
                    for pad in footprint.Pads():
                        for face in (p.F_Cu, p.B_Cu):
                            if not pad.IsOnLayer(face):
                                continue
                            shape = pad.GetEffectiveShape(face)
                            box = shape.BBox()
                            hits = sorted(set(preproof.collisions(shape, face, pad.GetNetname())))
                            item = {'uuid': pad.m_Uuid.AsString(), 'kind': 'PAD',
                                    'reference': footprint.GetReference(), 'number': pad.GetNumber(),
                                    'net': pad.GetNetname(), 'layer': int(face),
                                    'layer_name': board.GetLayerName(face),
                                    'copper_bounds_nm': [box.GetLeft(), box.GetTop(), box.GetRight(), box.GetBottom()],
                                    'collision_ids': hits}
                            details['objects'].append(item)
                            if hits:
                                details['failures'].append(item)
                route_width = int(board.GetDesignSettings().m_TrackMinWidth)
                barrier_shapes = [metadata['snapshot']['tracks'][metadata['track_ids'][label]]
                                  for label in ('top_trunk', 'bottom_trunk', 'back_wall')]
                barrier_width = min(q['width'] for q in barrier_shapes)
                bounds = metadata['input_geometry_nm']['board_bounds']
                barrier_insets = []
                for face in (p.F_Cu, p.B_Cu):
                    ys = [q[key][1] for q in barrier_shapes if q['layer'] == face for key in ('start', 'end')]
                    barrier_insets.extend((min(ys) - bounds[1], bounds[3] - max(ys)))
                barrier_end_inset = max(barrier_insets)
                pad_objects = {q['uuid']: q for q in details['objects'] if q['kind'] == 'PAD'}
                left_box = pad_objects[metadata['pad_ids']['signal_a']]['copper_bounds_nm']
                right_box = pad_objects[metadata['pad_ids']['signal_b']]['copper_bounds_nm']
                signal_pad_outer_inset = max(left_box[0] - bounds[0], bounds[2] - right_box[2])
                minimum_legal_center = preproof.edge_clearance + route_width / 2
                barrier_escape_maximum = barrier_end_inset - (barrier_width + route_width) / 2 - preproof.minimum_clearance
                pad_escape_maximum = signal_pad_outer_inset - route_width / 2 - preproof.minimum_clearance
                details['edge_escape_bounds_nm'] = {
                    'routing_width': route_width, 'barrier_width': barrier_width,
                    'actual_barrier_end_inset_maximum': barrier_end_inset,
                    'actual_signal_pad_outer_inset_maximum': signal_pad_outer_inset,
                    'minimum_legal_route_center_inset': minimum_legal_center,
                    'maximum_barrier_end_escape_center_inset': barrier_escape_maximum,
                    'maximum_signal_pad_outer_escape_center_inset': pad_escape_maximum,
                    'barrier_escape_blocked': barrier_escape_maximum < minimum_legal_center,
                    'signal_pad_escape_blocked': pad_escape_maximum < minimum_legal_center,
                }
                # These are conservative bounds: native edge-shape thickness
                # or stronger object clearance can only reduce free space.
                topology_ok = barrier_escape_maximum < minimum_legal_center and pad_escape_maximum < minimum_legal_center
                write_json(folder / 'input-preflight.json', details)
                preproof.assert_unchanged()
                if details['failures']:
                    raise ValueError('Independent fixture physical preflight failed: ' + json.dumps(details['failures'], sort_keys=True))
                if not topology_ok:
                    raise ValueError('Independent fixture no longer blocks edge escapes: ' + json.dumps(details['edge_escape_bounds_nm'], sort_keys=True))
                details['passed'] = True
                write_json(folder / 'input-preflight.json', details)
                row['fixture_native_preflight'] = True
                groups, pads, index = graph_sets(board, metadata['spec']['nets'].values(), time.monotonic() + 8, p, copper_graph)
                field_members = [metadata['track_ids'][k] for k in ('top_trunk', 'gate', 'bottom_trunk', 'pad_branch', 'padless_spur')]
                assert len({index[u] for u in field_members}) == 1
                # On a separate in-memory board, prove the actual retained
                # spur is padless and the third-pad branch detaches on removal.
                detached = p.LoadBoard(str(board_path))
                detached.Remove(next(t for t in detached.GetTracks() if t.m_Uuid.AsString() == metadata['track_ids']['gate']))
                split_groups, split_pads, split_index = graph_sets(detached, metadata['spec']['nets'].values(), time.monotonic() + 8, p, copper_graph)
                spur_group = next(q for q in split_groups if metadata['track_ids']['padless_spur'] in q)
                all_pad_ids = frozenset().union(*split_pads)
                assert not spur_group & all_pad_ids
                assert len({split_index[metadata['track_ids'][k]] for k in ('top_trunk', 'bottom_trunk', 'pad_branch', 'padless_spur')}) == 4
                row['before_removal_obligations'] = {'four_retained_fragments': True, 'actual_padless_fragment': sorted(spur_group)}
                request = {'operation': 'joint_repair', 'board': str(board_path),
                           'generated_ids': metadata['generated_ids'], 'protected_ids': metadata['protected_ids'],
                           'generation_profile': 'dsn-process-v2', 'routing_quality': 'turns', 'budget_s': args.budget_s}
                write_json(folder / 'request.json', request)
                print(json.dumps({'case': name, 'stage': 'public-joint-repair', 'board': str(board_path)}), flush=True)
                row['product_operation_called'] = True
                result = run(request)
                write_json(folder / 'native-result.json', result)
                row['native_result'] = result
                if metadata['spec']['expected_accepted']:
                    row['accepted_as_expected'] = result.get('accepted') is True
                    if row['accepted_as_expected']:
                        row['audit'] = audit_case(project, metadata, result, groups, pads,
                            (p, snapshot, copper_graph, verify_native, load, FrozenProofRouter, saved_via_clear_cached))
                        row['future_plane_audit'] = audit_future_plane(folder, metadata, (p, snapshot, copper_graph))
                        row['short_bus_audit'] = audit_short_bus_tail(folder, metadata, row['audit'], (p, snapshot, copper_graph))
                        row['passed'] = row['audit']['passed'] and row['future_plane_audit']['passed'] and row['short_bus_audit']['passed']
                    else:
                        row['rejected_files_exact'] = all(path.read_bytes() == content for path, content in original.items())
                else:
                    row['rejected_as_expected'] = result.get('accepted') is False
                    row['rejected_files_exact'] = all(path.read_bytes() == content for path, content in original.items())
                    row['passed'] = row['rejected_as_expected'] and row['rejected_files_exact']
        except Exception as error:
            row['error'] = {'type': type(error).__name__, 'message': str(error)}
        row['elapsed_s'] = time.monotonic() - started
        write_json(folder / 'case-result.json', row)
        write_json(args.output / 'result.json', report)
        print(json.dumps({'case': name, 'passed': row['passed'], 'elapsed_s': row['elapsed_s']}), flush=True)
    cross = {}
    if 'base' in report['cases'] and 'changed' in report['cases']:
        a, b = (report['cases'][n] for n in ('base', 'changed'))
        if a.get('audit') and b.get('audit'):
            first, second = (json.loads((args.output / n / 'fixture.json').read_text()) for n in ('base', 'changed'))
            cross = {'all_track_identities_changed': not set(first['generated_ids']) & set(second['generated_ids']),
                     'all_net_names_changed': not set(first['spec']['nets'].values()) & set(second['spec']['nets'].values()),
                     'opposite_blocker_geometry': first['spec']['shield_side'] != second['spec']['shield_side'],
                     'different_normalized_output_geometry': a['audit']['normalized_new_geometry'] != b['audit']['normalized_new_geometry']}
        else:
            cross = {'both_positive_cases_accepted': False}
    report['cross_case_checks'] = cross
    report['passed'] = all(row['passed'] for row in report['cases'].values()) and all(cross.values())
    write_json(args.output / 'result.json', report)
    print(json.dumps({'passed': report['passed'], 'output': str(args.output)}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
