import json
from pathlib import Path
import subprocess
import sys

import pytest
from smilepcb.runtime import SUPPORT

from smilepcb.keyboard_layout import parse_layout
from smilepcb.keyboard_model import design

ROOT=Path(__file__).resolve().parents[1]


def model():
    fixture=ROOT/'recipes/stm32/inputs/compact68'
    return design(parse_layout((fixture/'layout.kle.json').read_text(),json.loads((fixture/'keyboard.json').read_text())),controller_origin_mm=(0,0))


@pytest.mark.skipif(
    not (SUPPORT / "symbols/MCU_ST_STM32F0.kicad_sym").is_file(),
    reason="Integration check needs the KiCad STM32 symbol library",
)
def test_staged_nets_follow_physical_pin_roles_and_the_selected_rgb_chain():
    from smilepcb.keyboard_phases import ordered_nets
    cfg=model();parts={part['ref']:part for part in cfg['parts']};mcu=parts['U_MCU']
    phases=ordered_nets(cfg)
    assert [name for name,_ in phases]==['control','rows','columns','rgb']
    groups=dict(phases)
    assert set(groups['rows'])=={mcu['nets'][cfg['mcu_gpio_pads'][pin]] for pin in cfg['layout']['gpio']['rows']}
    assert set(groups['columns'])=={mcu['nets'][cfg['mcu_gpio_pads'][pin]] for pin in cfg['layout']['gpio']['cols']}
    chain=cfg['layout']['rgb_order']
    expected=[parts['LED'+str(chain[0])]['nets']['3']]+[parts['LED'+str(key)]['nets']['2'] for key in chain[:-1]]
    assert groups['rgb']==expected
    renamed={net:'renamed channel '+str(index) for index,net in enumerate(set().union(*(set(nets) for _,nets in phases)))}
    for part in cfg['parts']:
        part['nets']={pin:renamed.get(net,net) for pin,net in part['nets'].items()}
    changed=dict(ordered_nets(cfg))
    for name in groups:assert set(changed[name])=={renamed.get(net,net) for net in groups[name]}


def test_native_staging_is_explicit_and_rejects_conflicting_modes_before_tools(tmp_path):
    script=ROOT/'recipes/stm32/generate.py'
    for flags,message in ((['--native-staged'],'requires an explicit process profile'),(['--native-staged','--process-profile','fr4-2l-1oz-0p5-0p2','--joint-board'],'cannot be combined'),(['--native-staged','--process-profile','fr4-2l-1oz-0p5-0p2','--stop-after-placement'],'cannot stop before controller interface verification')):
        result=subprocess.run([sys.executable,str(script),'missing-tools','unused-tag',*flags],cwd=tmp_path,capture_output=True,text=True)
        assert result.returncode==2,result.stderr
        assert message in result.stderr,result.stderr
        assert not list(tmp_path.iterdir())
