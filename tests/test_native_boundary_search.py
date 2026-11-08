"""Native routing behavior, independent of the controller's candidate templates."""
import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('case',('east','west','north','south','blocked','via','expired'))
def test_native_boundary_goal_keeps_contact_clearance_and_outward_heading(tmp_path,case):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path/'result.json'
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_boundary_search_probe.py')),case,str(out)],
        cwd=root,env={**os.environ,'PYTHONPATH':str(root),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=15)
    assert run.returncode==0,run.stdout+run.stderr
    report=json.loads(out.read_text())
    assert report.get('available'),report
    if case in ('blocked','expired'):
        assert not report['connected'] and report['unchanged'],report
    else:
        assert report['connected'] and report['all_added_connected'] and report['retained_exact'],report
        assert report['outside'] and report['angle_safe'] and report['continuation_clear'],report
        if case=='via':assert report['vias']>=1 and report['exit_on_opposite_face'],report
