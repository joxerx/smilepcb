"""Actual native parity for completed-board owner-exclusion reuse."""
import json
import os
from pathlib import Path
import subprocess
import pytest

@pytest.mark.parametrize('case',('parity','same-net-contributor','exception','invalid-nested','seal','expiry','stale-launch','fixed-launch','integration'))
def test_completed_controller_proof_reuses_exact_native_owner_oracle(tmp_path,case):
    native=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not native:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];output=tmp_path/'result.json'
    run=subprocess.run([native,str(Path(__file__).with_name('native_controller_proof_probe.py')),case,str(output)],cwd=root,
        env={**os.environ,'PYTHONPATH':str(root),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=20)
    assert run.returncode==0,run.stdout+run.stderr
    assert json.loads(output.read_text())['passed']
