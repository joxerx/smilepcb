"""A preserved power block may coexist with fresh GPIO allocation; matrix copper may not."""
import json,os,subprocess,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]

def test_cli_accepts_preserved_core_and_gpio_allocation_then_validates_inputs(tmp_path):
    r=subprocess.run([sys.executable,str(ROOT/'recipes/stm32/generate.py'),str(tmp_path/'missing-tools.json'),'parse-only-routed-core',
        '--fixture','ansi61','--native-staged','--preserve-core-routing','--optimize-matrix-gpio','--process-profile','fr4-2l-1oz-0p5-0p2'],cwd=ROOT,capture_output=True,text=True,timeout=10)
    assert 'Matrix reassignment requires releasing copied matrix copper' not in r.stderr
    assert r.returncode==1 and 'Toolchain configuration must be a bounded JSON file' in r.stderr

def test_native_gpio_guard_retains_power_copper_and_rejects_matrix_copper(tmp_path):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    r=subprocess.run([interpreter,str(Path(__file__).with_name('native_routed_core_gpio_probe.py'))],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT)},capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stdout+r.stderr
    result=json.loads(r.stdout)
    assert result=={'power_unchanged':True,'power_pin_unchanged':True,'matrix_rejected':True,'rejection_unchanged':True}
