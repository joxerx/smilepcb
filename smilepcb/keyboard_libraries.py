"""Materialize pinned public component data into a self-contained CAD project."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from . import schematic as s
from .keyboard_model import SW_SYMBOL, LED_SYMBOL

DATA = Path(__file__).with_name('data') / 'keyboard'


def create_libraries(project):
    manifest = json.loads((DATA/'sources.json').read_text())
    for entry in manifest:
        if hashlib.sha256((DATA/entry['filename']).read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Packaged public component source changed: '+entry['filename'])
    folder = project/'KeyboardParts.pretty'
    folder.mkdir()
    switch = s.read(DATA/'SW_MX_HS_CPG151101S11_1u.kicad_mod')
    switch[1] = 'MX_HS'; s.write(folder/'MX_HS.kicad_mod', switch)
    cutout = s.read(DATA/'LED_MX_6028R.kicad_mod')
    cutout[1] = 'MINI_E_CUTOUT'; s.write(folder/'MINI_E_CUTOUT.kicad_mod', cutout)
    led = deepcopy(cutout); led[1] = 'MINI_E'
    led[:] = [n for n in led if not (s.tag(n) in ('fp_line','fp_arc') and s.one(n,'layer')[1] == 'Edge.Cuts')]
    s.write(folder/'MINI_E.kicad_mod', led)
    for size in ('2.00','6.25'):
        original = s.SUPPORT/'footprints/Button_Switch_Keyboard.pretty'/('SW_Cherry_MX_'+size+'u_PCB.kicad_mod')
        src = s.read(original)
        tree = [s.S('footprint'),'STAB_'+size,[s.S('version'),20240108],
                [s.S('generator'),s.S('pcbnew')],[s.S('layer'),'F.Cu'],
                [s.S('descr'),'PCB-mount MX stabilizer holes derived from '+original.name]]
        xs = []
        for pad in s.children(src,'pad'):
            at = s.one(pad,'at')
            if str(pad[2]) != 'np_thru_hole' or abs(at[1]+2.54) < 8: continue
            copy = deepcopy(pad); pos = s.one(copy,'at')
            pos[1] = round(pos[1]+2.54,6); pos[2] = round(pos[2]-5.08,6)
            tree.append(copy); xs.append(pos[1])
        if len(xs) != 4: raise ValueError('Unexpected standard stabilizer geometry')
        # Separate local courtyards leave the switch's stem/socket envelope free.
        for x in sorted(set(xs)):
            corners = [(x-2.25,-9),(x+2.25,-9),(x+2.25,10.5),(x-2.25,10.5)]
            for a,b in zip(corners,corners[1:]+corners[:1]):
                tree.append([s.S('fp_line'),[s.S('start'),*a],[s.S('end'),*b],
                             [s.S('stroke'),[s.S('width'),.05],[s.S('type'),s.S('default')]],
                             [s.S('layer'),'F.CrtYd']])
        s.write(folder/('STAB_'+size+'.kicad_mod'),tree)
    source = s.read(DATA/'marbastlib-mx.kicad_sym')
    symbols = {}
    for original, name in [('MX_SW_HS_CPG151101S11',SW_SYMBOL),('MX_SK6812MINI-E',LED_SYMBOL)]:
        node = deepcopy(next(n for n in s.children(source,'symbol') if n[1] == original))
        bare = name.split(':')[1]
        node[1] = name
        for unit in s.children(node,'symbol'):
            unit[1] = unit[1].replace(original+'_',bare+'_',1)
        for prop in s.children(node,'property'):
            if prop[1] == 'Footprint': prop[2] = name
        symbols[name] = node
    library = [s.S('kicad_symbol_lib'),[s.S('version'),20231120],[s.S('generator'),s.S('kicad_symbol_editor')]]
    for name, node in symbols.items():
        node = deepcopy(node); node[1] = name.split(':')[1]; library.append(node)
    s.write(project/'KeyboardParts.kicad_sym',library)
    (project/'fp-lib-table').write_text('(fp_lib_table (lib (name "KeyboardParts") (type "KiCad") (uri "${KIPRJMOD}/KeyboardParts.pretty") (options "") (descr "Pinned public keyboard parts")))\n')
    (project/'sym-lib-table').write_text('(sym_lib_table (lib (name "KeyboardParts") (type "KiCad") (uri "${KIPRJMOD}/KeyboardParts.kicad_sym") (options "") (descr "Pinned public keyboard symbols")))\n')
    shutil.copy2(DATA/'LICENSE',project/'LICENSE-marbastlib')
    (project/'component-sources.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return symbols
