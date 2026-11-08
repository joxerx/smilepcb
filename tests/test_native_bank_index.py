import json,os,subprocess
from pathlib import Path
import pytest


@pytest.mark.parametrize('case',['connected','clearance','lifecycle','setup-expiry'])
def test_native_bank_grid_index_is_append_only_and_scoped(tmp_path,case):
    native=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not native:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];output=tmp_path/'result.json'
    run=subprocess.run([native,str(Path(__file__).with_name('native_bank_index_probe.py')),case,str(output)],cwd=root,
        env={**os.environ,'PYTHONPATH':str(root),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=35)
    assert run.returncode==0,run.stdout+run.stderr
    assert json.loads(output.read_text())['retained_exact']
