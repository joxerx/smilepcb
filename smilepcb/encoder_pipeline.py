"""Incremental encoder probe on one explicitly retained synthetic board only."""
import csv
import json
import math
from pathlib import Path
import shutil
import time
import xml.etree.ElementTree as ET

from smilepcb import schematic as s
from smilepcb.circuit import part
from smilepcb.service import encode, native, sha, footprint_digests
from smilepcb.lab_tools import worker
from smilepcb.lab_tools import export_netlist, rule_checks
from smilepcb.lab_tools import run_router, JAR_SHA256
from smilepcb.routing_checks import verify_preservation, require_clean_rules
from smilepcb.routing_exchange import routing_dsn
from smilepcb.routing_policy import prioritize_dsn, residual_nets
from smilepcb.encoder_recipe import (FOOTPRINT, MODEL, available_gpio, placements,
                                                    encoder_qmk, verify_composition, reviewed_footprint, power_allocation)
from smilepcb.firmware import read_firmware

NAME = 'SmilePCB transfer 3x4'
REPO = Path(__file__).resolve().parents[1]
BRIDGE = Path(__file__).with_name('encoder_native.py')


def run(source, output, *, input_files, power_profile):
    source = Path(source).resolve()
    started = time.perf_counter()
    composition = compose(source, output, input_files=input_files, power_profile=power_profile)
    output = Path(output).resolve()
    project = Path(composition['project'])
    board = project / (NAME + '.kicad_pcb')
    seed = native('inspect', board)
    seed_footprints = footprint_digests(board)
    route_worker = Path(__file__).with_name('routing_native.py')

    def bridge(operation, **kwargs):
        return worker(route_worker, {'operation': operation, 'key_count': 12, 'encoder_fixture': True,
                      'board': str(board), **kwargs}, output / (operation + '.log'))

    bridge('export', dsn=str(output / 'board.raw.dsn'))
    report = json.loads((output / 'composed-rules/drc.json').read_text())
    wanted = residual_nets(report, seed)
    assert set(wanted) == {new for old, new in composition['pad_net_changes'].values()}
    # Native DSN keepouts are left conservative; no footprint-specific hole
    # shrinking. Deferred classes remain obstacles but are not routed again.
    dsn, ignored = prioritize_dsn(routing_dsn((output / 'board.raw.dsn').read_text()), wanted)
    (output / 'board.dsn').write_text(dsn)
    tools = REPO / '.lab/routing-toolchain'
    import os
    jar = Path(os.environ.get('SMILEPCB_ROUTER_JAR', tools / 'freerouting-2.5.0.jar'))
    java = Path(os.environ.get('SMILEPCB_ROUTER_JAVA', tools / 'java25/jdk-25.0.4.1+1-jre/Contents/Home/bin/java'))
    assert sha(jar) == JAR_SHA256
    resources = run_router(output, java, jar,
                           ignore_classes=ignored)
    final = bridge('import', ses=str(output / 'board.ses'))
    preservation = verify_preservation(seed, final)
    assert seed_footprints == footprint_digests(board), 'Routing changed full footprint content'
    added = [v for uid, v in final['tracks'].items() if uid not in seed['tracks']]
    assert all(v['net'] in wanted for v in added), 'Router touched a retained net'
    rules = rule_checks(project, output / 'final-rules', NAME)
    require_clean_rules(rules)
    pins, _, _ = export_netlist(project, output / 'final.xml', NAME)
    original, _, _ = export_netlist(source, output / 'source.xml', NAME)
    allowed = {('U_MCU', n): new for n, (old, new) in composition['pad_net_changes'].items()}
    assert {key: pins[key] for key in original} == {key: allowed.get(key, net) for key, net in original.items()}
    assert set(pins) - set(original) == {('ENC1', pin) for pin in ('A', 'B', 'C')}
    for fp in final['footprints'].values():
        for pad in fp['pads']:
            if (fp['ref'], pad['number']) in pins:
                assert pins[(fp['ref'], pad['number'])] == pad['net']
    before_tree, after_tree = s.read(source / board.name), s.read(board)
    for kind in ('gr_line', 'gr_arc', 'gr_rect'):
        assert s.children(before_tree, kind) == s.children(after_tree, kind), 'Board outline or graphics changed'
    zones = lambda t: [[item for item in zone if s.tag(item) not in ('filled_polygon', 'fill_segments')]
                       for zone in s.children(t, 'zone')]
    assert zones(before_tree) == zones(after_tree), 'Retained zone definition changed'
    for suffix in ('.kicad_pro', '.kicad_dru'):
        assert (source / (NAME + suffix)).read_bytes() == (project / (NAME + suffix)).read_bytes()
    assert composition['source_sha256'] == source_hashes(source, input_files)
    xml = ET.parse(output / 'final.xml')
    with (project / 'BOM.csv').open('w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['Reference', 'Value', 'Footprint'])
        for component in sorted(xml.findall('./components/comp'), key=lambda c: c.attrib['ref']):
            writer.writerow([component.attrib['ref'], component.findtext('value'), component.findtext('footprint')])
    result = {**composition, 'native_erc_drc_clean': True, 'final_rules': rules,
              'preservation': preservation, 'new_copper_nets': sorted({v['net'] for v in added}),
              'manual_signal_segments': 0, 'inherited_policy_unchanged': True,
              'schematic_pcb_pin_assignments_checked': len(pins), 'resources': resources,
              'elapsed_s': time.perf_counter() - started, 'firmware_built': False,
              'hardware_tested': False, 'fabrication_ready': False, 'electrical_power_verified': False,
              'encoder': {'model': MODEL, 'resolution': 4, 'push_switch': False,
                          'internal_pullups': True, 'filtering': 'QMK quadrature decoder; no external RC',
                          'mechanical_obround_terminal_fit_verified': False,
                          'direction_and_noise_immunity_tested': False}}
    (output / 'result.json').write_text(encode(result))
    return result


def source_hashes(source, names):
    source = Path(source)
    read_firmware(source)
    return {name: sha(source / name) for name in names}


def mcu_context(tree):
    instance = next(n for n in s.children(tree, 'symbol') if any(
        p[1:3] == ['Reference', 'U_MCU'] for p in s.children(n, 'property')))
    assert s.one(instance, 'lib_id')[1] == 'MCU_Module:RaspberryPi_Pico'
    assert s.one(instance, 'at')[3] == 0
    x, y = s.one(instance, 'at')[1:3]
    embedded = next(n for n in s.children(s.one(tree, 'lib_symbols'), 'symbol')
                    if n[1] == 'MCU_Module:RaspberryPi_Pico')
    names, positions = {}, {}
    for unit in s.children(embedded, 'symbol'):
        for pin in s.children(unit, 'pin'):
            number = str(s.one(pin, 'number')[1])
            names[number] = str(s.one(pin, 'name')[1])
            px, py = s.one(pin, 'at')[1:3]
            positions[number] = [round(x + px, 6), round(y - py, 6)]
    return names, positions


def compose(source, output, *, input_files, power_profile):
    source = Path(source).resolve()
    output = Path(output).resolve()
    hashes = source_hashes(source, input_files)
    power = power_allocation(power_profile)
    output.mkdir(parents=True, exist_ok=False)
    project = output / 'project'
    project.mkdir()
    for name in input_files:
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    footprint = reviewed_footprint(project)
    board, sch = [project / (NAME + suffix) for suffix in ('.kicad_pcb', '.kicad_sch')]
    before = native('inspect', board)
    context = worker(BRIDGE, {'operation': 'context', 'board': str(board), 'footprint': FOOTPRINT}, output / 'context.log')
    tree = s.read(sch)
    names, sch_positions = mcu_context(tree)
    nc = {number for number, at in sch_positions.items() if any(
        s.one(node, 'at')[1:3] == at for node in s.children(tree, 'no_connect'))}
    qmk = json.loads((project / 'qmk/keyboard.json').read_text())
    pads = next(f for f in before['footprints'].values() if f['ref'] == 'U_MCU')['pads']
    used = set(qmk['matrix_pins']['rows'] + qmk['matrix_pins']['cols'] + [qmk['ws2812']['pin']])
    free = available_gpio(names, pads, used, {t['net'] for t in before['tracks'].values()}, nc)
    if len(free) < 2:
        raise ValueError('Fewer than two attested unused exposed GPIO')
    positions = placements(context['outline'], context['encoder_box'], context['components'], context['copper'])
    if not positions:
        raise ValueError('Encoder does not fit without moving retained geometry')
    # Jointly choose placement and two distinct GPIO by shortest straight-line
    # estimate; the native router still determines actual geometry and vias.
    candidates = []
    for xy in positions:
        for a in free:
            for b in free:
                if a['pin'] == b['pin']:
                    continue
                score = sum(math.dist([point['position'][i] / 1e6 for i in (0, 1)],
                                      [xy[i] + context['encoder_pads'][channel][i] for i in (0, 1)])
                            for point, channel in ((a, 'A'), (b, 'B')))
                candidates.append((score, xy, a, b))
    _, xy, a, b = min(candidates, key=lambda v: (v[0], v[1], int(v[2]['gpio'][2:]), int(v[3]['gpio'][2:])))
    changes = {a['pin']: [a['old_net'], 'ENC_A'], b['pin']: [b['old_net'], 'ENC_B']}
    for number, (_, net) in changes.items():
        matching = [n for n in s.children(tree, 'no_connect') if s.one(n, 'at')[1:3] == sch_positions[number]]
        assert len(matching) == 1, 'Ambiguous MCU no-connect marker'
        tree.remove(matching[0])
        x, y = sch_positions[number]
        tree.append(s.label('global_label', net, x, y, 'label/U_MCU/encoder/' + number))
    sx = max(s.one(n, 'at')[1] for n in s.children(tree, 'symbol')) + 35
    addition = part('ENC1', 'Device:RotaryEncoder', FOOTPRINT, {'A': 'ENC_A', 'B': 'ENC_B', 'C': 'GND'},
                    xy, (sx, 40), MODEL, reference_text={'offset': [3, -4.75], 'angle': 0})
    s.add_parts(tree, [addition], '/' + str(s.one(tree, 'uuid')[1]))
    s.one(s.one(s.children(tree, 'symbol')[-1], 'instances'), 'project')[1] = NAME
    s.write(sch, tree)
    pins, paths, _ = export_netlist(project, output / 'composed.xml', NAME)
    after = worker(BRIDGE, {'operation': 'compose', 'board': str(board), 'part': addition,
                           'path': paths['ENC1'], 'changes': changes}, output / 'compose.log')
    verify_composition(before, after, changes)
    # Cover all retained footprint properties, texts and thermal overrides too.
    # Only net names on the two selected pad objects are normalized for comparison.
    original_tree = s.read(source / board.name)
    for fp in s.children(original_tree, 'footprint'):
        if not any(p[1:3] == ['Reference', 'U_MCU'] for p in s.children(fp, 'property')):
            continue
        for pad in s.children(fp, 'pad'):
            if pad[1] in changes:
                s.one(pad, 'net')[-1] = changes[pad[1]][1]
    expected = output / 'expected-retained.kicad_pcb'
    s.write(expected, original_tree)
    full_before, full_after = footprint_digests(expected), footprint_digests(board)
    assert all(full_after[uid] == digest for uid, digest in full_before.items()), 'Retained footprint property changed'
    for fp in after['footprints'].values():
        for pad in fp['pads']:
            if pad['number'] and (fp['ref'], pad['number']) in pins:
                assert pins[(fp['ref'], pad['number'])] == pad['net']
    (project / 'qmk/keyboard.json').write_text(encode(encoder_qmk(qmk, a['gpio'], b['gpio'])))
    manifest = json.loads((project / 'firmware-manifest.json').read_text())
    manifest['files_sha256'] = {name: sha(project / 'qmk' / name) for name in manifest['files_sha256']}
    manifest['encoder_profile'] = MODEL
    (project / 'firmware-manifest.json').write_text(encode(manifest))
    read_firmware(project)
    rules = rule_checks(project, output / 'composed-rules', NAME)
    assert hashes == source_hashes(source, input_files), 'Retained input changed'
    result = {'project': str(project), 'source_sha256': hashes, 'source_unchanged': True,
              'gpio': [a, b], 'pad_net_changes': changes, 'placement_mm': xy, 'power_model': power,
              'reviewed_footprint_sha256': sha(footprint),
              'placement_candidate_count': len(positions), 'part': addition, 'composed_rules': rules,
              'retained_copper': len(before['tracks']), 'retained_full_footprints': len(full_before)}
    (output / 'composition.json').write_text(encode(result))
    return result
