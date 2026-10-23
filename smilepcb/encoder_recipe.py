"""Planning and preservation rules for the selected synthetic encoder experiment."""
from copy import deepcopy
import math
import re

BASE_FOOTPRINT = 'Rotary_Encoder:RotaryEncoder_Bourns_Vertical_PEC12R-3x17F-Nxxxx'
FOOTPRINT = 'SmilePCB_Lab:PEC12R_3x17F_Nxxxx_Reviewed'
MODEL = 'PEC12R-3217F-N0024'


def reviewed_footprint(project):
    from pathlib import Path
    from smilepcb import schematic as s
    from smilepcb.runtime import SUPPORT
    lib, name = BASE_FOOTPRINT.split(':')
    tree = s.read(SUPPORT / 'footprints' / (lib + '.pretty') / (name + '.kicad_mod'))
    tree[1] = FOOTPRINT.split(':')[1]
    s.one(tree, 'descr')[1] = ('PEC12R-3xxxF-Nxxxx; mounting reviewed against Bourns PEC12R Rev 07/21, '
                              'page 3; 13.2 mm lug pitch. No verified STEP model.')
    mounts = sorted((n for n in s.children(tree, 'pad') if n[1] == 'SH'), key=lambda n: s.one(n, 'at')[2])
    assert len(mounts) == 2
    for pad, y in zip(mounts, (-4.1, 9.1)):
        s.one(pad, 'at')[1:3] = [7.5, y]
        # Rotate the manufacturer's window axes with the A/C/B column:
        # X=2.6..2.8, Y=2.0..2.2 mm; add a 0.5 mm copper annulus.
        # Its rounded corners still need a terminal-fit review: the drawing
        # depicts rectangular windows, not an approved obround land pattern.
        s.one(pad, 'drill')[2:4] = [2.8, 2.2]
        s.one(pad, 'size')[1:3] = [3.8, 3.2]
    tree[:] = [n for n in tree if s.tag(n) != 'model' and not
               (s.tag(n) == 'fp_line' and s.one(n, 'layer')[1] == 'F.CrtYd')]
    # New courtyard encloses the enlarged mechanical pad extent, existing
    # body and signal pads with at least 0.25 mm assembly margin.
    points = [(-1.1, -6.25), (14.5, -6.25), (14.5, 11.25), (-1.1, 11.25)]
    for index, (a, b) in enumerate(zip(points, points[1:] + points[:1])):
        tree.append([s.S('fp_line'), [s.S('start'), *a], [s.S('end'), *b],
                     [s.S('stroke'), [s.S('width'), .05], [s.S('type'), s.S('solid')]],
                     [s.S('layer'), 'F.CrtYd'], [s.S('uuid'), s.identity('encoder/courtyard/' + str(index))]])
    project = Path(project)
    directory = project / 'SmilePCB_Lab.pretty'
    directory.mkdir()
    path = directory / (tree[1] + '.kicad_mod')
    s.write(path, tree)
    table = project / 'fp-lib-table'
    entries = s.read(table) if table.exists() else [s.S('fp_lib_table')]
    if any(s.one(lib, 'name')[1] == 'SmilePCB_Lab' for lib in s.children(entries, 'lib')):
        raise ValueError('Reviewed encoder library already exists')
    entries.append([s.S('lib'), [s.S('name'), 'SmilePCB_Lab'], [s.S('type'), 'KiCad'],
                    [s.S('uri'), '${KIPRJMOD}/SmilePCB_Lab.pretty'], [s.S('options'), ''],
                    [s.S('descr'), 'Reviewed synthetic encoder block']])
    # The KiCad 10 table-type sniffer requires '(fp_lib_table' on line one.
    # Schematic pretty printing inserts a newline after '(' and is unsuitable.
    table.write_text(s.sx.dumps(entries) + '\n')
    return path


def power_allocation(previous):
    result = {'inherited_required_ma': previous['required_budget_ma'], 'additional_encoder_allocation_ma': 1,
              'required_ma': previous['required_budget_ma'] + 1, 'supply_budget_ma': previous['supply_budget_ma'],
              'basis': 'engineering allocation; neither measured nor verified maximum'}
    if result['required_ma'] > result['supply_budget_ma']:
        raise ValueError('Encoder engineering budget exceeds the selected supply budget')
    return result


def validate_encoder_fixture(footprints, key_count):
    expected = {prefix + str(i) for prefix in ('SW', 'D', 'LED', 'C_LED') for i in range(1, 13)}
    expected.update(('U_MCU', 'U_RGB', 'R_RGB', 'R_PD', 'C_RGB_BUF', 'ENC1'))
    if key_count != 12 or len(footprints) != 54 or {f['ref'] for f in footprints} != expected:
        raise ValueError('Expected only the selected 3x4 encoder fixture')
    if next(f for f in footprints if f['ref'] == 'ENC1')['footprint'] != FOOTPRINT:
        raise ValueError('Unexpected encoder footprint')


def available_gpio(names, pads, occupied, copper_nets, nc_pins):
    result = []
    for pad in pads:
        number, net = pad['number'], pad['net']
        match = re.fullmatch(r'GPIO(\d+)(?:_ADC\d+)?', names.get(number, ''))
        if not match:
            continue
        gpio = 'GP' + match[1]
        expected = f'unconnected-(U_MCU-{names[number]}-Pad{number})'
        if gpio in occupied or net != expected or net in copper_nets or number not in nc_pins:
            continue
        result.append({'pin': number, 'gpio': gpio, 'old_net': net, 'position': pad['position']})
    return sorted(result, key=lambda p: int(p['gpio'][2:]))


def placements(outline, local_box, components, copper, *, margin=.75, step=1):
    if step <= 0 or margin < 0:
        raise ValueError('Invalid placement grid or edge margin')
    x0, y0, x1, y1 = outline
    a, b, c, d = local_box
    result = []
    for ix in range(math.ceil((x0 + margin - a) / step), math.floor((x1 - margin - c) / step) + 1):
        for iy in range(math.ceil((y0 + margin - b) / step), math.floor((y1 - margin - d) / step) + 1):
            x, y = ix * step, iy * step
            box = [x + a, y + b, x + c, y + d]
            if all(box[2] < other[0] or other[2] < box[0] or box[3] < other[1] or other[3] < box[1]
                   for other in (*components, *copper)):
                result.append([x, y])
    return result


def verify_composition(before, after, changes):
    assert before['tracks'] == after['tracks'], 'Composition changed retained copper'
    allowed = deepcopy(before['footprints'])
    seen = set()
    for fp in allowed.values():
        if fp['ref'] != 'U_MCU':
            continue
        for pad in fp['pads']:
            if pad['number'] in changes:
                old, new = changes[pad['number']]
                assert pad['net'] == old and old.startswith('unconnected-(U_MCU-')
                assert all(t['net'] != old for t in before['tracks'].values())
                pad['net'] = new
                seen.add(pad['number'])
    assert seen == set(changes), 'Missing selected MCU pad'
    assert {uid: after['footprints'].get(uid) for uid in allowed} == allowed, 'Retained component changed'
    extra = [fp for uid, fp in after['footprints'].items() if uid not in allowed]
    assert len(extra) == 1 and extra[0]['ref'] == 'ENC1', 'Unexpected new component'


def encoder_qmk(existing, a, b):
    used = set(existing['matrix_pins']['rows'] + existing['matrix_pins']['cols'])
    used.add(existing.get('ws2812', {}).get('pin'))
    if 'encoder' in existing or a == b or a in used or b in used:
        raise ValueError('Encoder already present or GPIO conflict')
    if not all(re.fullmatch(r'GP\d+', pin) for pin in (a, b)):
        raise ValueError('Expected exposed Pico GPIO names')
    result = deepcopy(existing)
    result.setdefault('features', {}).update(encoder=True, extrakey=True)
    result['encoder'] = {'rotary': [{'pin_a': a, 'pin_b': b, 'resolution': 4}]}
    return result
