"""Strict KLE geometry and automatic matrix allocation for wired STM32 boards."""
from decimal import Context, Decimal, DecimalException, localcontext
import hashlib
import re

from .kle import COSMETIC, META, document, number

PROFILE = 'stm32f072-mx-mini-e-udb-c5'
ROW_PINS = ('B0', 'B1', 'B2', 'B10', 'B11', 'B12', 'B13', 'B14')
COL_PINS = ('A0', 'A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7',
            'A15', 'B3', 'B4', 'B5', 'B6', 'B7', 'B13', 'B14',
            'B15', 'C13', 'C14', 'C15', 'F0')
WIDTHS = {Decimal(str(v)) for v in (1, 1.25, 1.5, 1.75, 2, 2.25, 2.75, 6.25)}


def mm(value):
    nm = value * 19050000
    if nm != nm.to_integral_value():
        raise ValueError('Key coordinate is finer than the native nanometre grid')
    return float(nm / 1000000)


def parse_layout(kle_text, configuration):
    """Preserve serialized identity; derive sparse matrix cells without a sidecar."""
    with localcontext(Context(prec=32)):
        try:
            return _parse(kle_text, configuration)
        except DecimalException as error:
            raise ValueError('Unsupported coordinate precision') from error


def _parse(text, config):
    if not isinstance(config, dict) or set(config) != {'schema', 'name', 'profile', 'keycodes', 'rgb'}:
        raise ValueError('Configuration requires schema, name, profile, keycodes and rgb')
    if type(config['schema']) is not int or config['schema'] != 1 or config['profile'] != PROFILE:
        raise ValueError('Unsupported keyboard schema or hardware profile')
    if not isinstance(config['name'], str) or not 1 <= len(config['name']) <= 80:
        raise ValueError('Keyboard name must contain 1 to 80 characters')
    if type(config['rgb']) is not bool:
        raise ValueError('RGB selection must be boolean')
    codes = config['keycodes']
    if not isinstance(codes, list) or any(not isinstance(c, str) or not re.fullmatch(r'KC_[A-Z0-9_]{1,32}', c) for c in codes):
        raise ValueError('Provide safe, explicit QMK KC_ identifiers')
    rows = document(text)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10:
        raise ValueError('Expected bounded complete KLE JSON')
    y = Decimal(0)
    raw = []
    row_index = 0
    for ri, row in enumerate(rows):
        if isinstance(row, dict):
            if ri or set(row) - META:
                raise ValueError('Unsupported KLE metadata')
            continue
        if not isinstance(row, list) or not row or not isinstance(row[-1], str):
            raise ValueError('Each KLE row must end in a key')
        x = Decimal(0)
        width = height = Decimal(1)
        column = 0
        for item in row:
            if isinstance(item, str):
                if len(item) > 256:
                    raise ValueError('Key legend is too long')
                raw.append((x, y, width, height, row_index, column, item))
                column += 1
                x += width
                width = height = Decimal(1)
                if len(raw) > 128:
                    raise ValueError('At most 128 keys are supported')
                continue
            if not isinstance(item, dict) or set(item) - ({'x', 'y', 'w', 'h'} | COSMETIC):
                raise ValueError('Unsupported KLE property: rotated or compound keys are not supported')
            for field, value in item.items():
                if field in ('x', 'y', 'w', 'h'):
                    value = number(value)
                    if field == 'x': x += value
                    elif field == 'y': y += value
                    elif field == 'w':
                        if value not in WIDTHS: raise ValueError('Unsupported MX key width')
                        width = value
                    else:
                        if value not in (1, 2): raise ValueError('Unsupported MX key height')
                        height = value
                elif field in ('c', 't', 'p'):
                    if not isinstance(value, str) or len(value) > 256: raise ValueError('Invalid cosmetic string')
                elif field == 'n':
                    if type(value) is not bool: raise ValueError('Invalid nub flag')
                elif field == 'fa':
                    if not isinstance(value, list) or len(value) > 12: raise ValueError('Invalid legend sizes')
                    for v in value: number(v)
                else:
                    number(value)
            number(x); number(y)
        row_index += 1
        y += 1
    if not raw or len(codes) != len(raw):
        raise ValueError('Provide one keycode per serialized KLE key')
    origin_x = min(v[0] for v in raw)
    origin_y = min(v[1] for v in raw)
    keys = []
    for index, (x, y, width, height, r, c, label) in enumerate(raw):
        x -= origin_x; y -= origin_y
        if width != 1 and height != 1:
            raise ValueError('Compound wide and tall keys are unsupported')
        if x + width > 32 or y + height > 10:
            raise ValueError('Keyboard exceeds the supported 32u by 10u envelope')
        for previous in keys:
            px, py = map(Decimal, map(str, previous['position_u']))
            pw, ph = map(Decimal, map(str, previous['size_u']))
            if x < px + pw and x + width > px and y < py + ph and y + height > py:
                raise ValueError('Overlapping key envelopes')
        keys.append({'id': index + 1, 'matrix': [r, c], 'position_u': [float(x), float(y)],
                     'size_u': [float(width), float(height)],
                     'center_mm': [mm(x + width / 2), mm(y + height / 2)],
                     'label': label, 'keycode': codes[index]})
    nr = row_index
    from .keyboard_matrix import align_matrix_columns
    nc = align_matrix_columns(keys,nr)
    if nr > len(ROW_PINS): raise ValueError('Too many electrical rows')
    row_pins = list(ROW_PINS[:nr])
    columns = [pin for pin in COL_PINS if pin not in row_pins]
    if nc > len(columns): raise ValueError('Insufficient reserved STM32 GPIOs')
    width = max(Decimal(str(k['position_u'][0])) + Decimal(str(k['size_u'][0])) for k in keys)
    height = max(Decimal(str(k['position_u'][1])) + Decimal(str(k['size_u'][1])) for k in keys)
    chain = [k['id'] for r in range(nr) for k in sorted(
        (k for k in keys if k['matrix'][0] == r), key=lambda k: k['center_mm'][0], reverse=bool(r % 2))]
    return {'name': config['name'], 'profile': PROFILE, 'rgb': config['rgb'], 'keys': keys,
            'rows': nr, 'cols': nc, 'size_mm': [mm(width), mm(height)], 'rgb_order': chain,
            'gpio': {'rows': row_pins, 'cols': columns[:nc], 'rgb_data': 'B8', 'rgb_enable': 'B9',
                     'rgb_output_enable': 'F1'},
            'kle_sha256': hashlib.sha256(text.encode()).hexdigest()}
