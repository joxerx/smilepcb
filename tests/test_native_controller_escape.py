import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('case',('valid','via','contract','zero-neck','blocked','expired','exception','neck'))
def test_generic_native_escape_preserves_obligations_and_transactions(tmp_path,case):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path/'report.json'
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_controller_escape_probe.py')),case,str(out)],
        cwd=root,env={**os.environ,'PYTHONPATH':str(root),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=25)
    assert run.returncode==0,run.stdout+run.stderr
    report=json.loads(out.read_text())
    assert report.get('available'),report
    if case=='neck':
        assert all(report['checks'].values()),report
    elif case in ('blocked','expired','exception'):
        assert not report['accepted'] and report['unchanged'],report
        if case=='exception':assert report['caught_exception'],report
    else:
        assert report['accepted'] and report['retained_exact'] and report['exact_owned_delta'],report
        assert report['ports']==2 and report['source_aliases']==3,report
        assert report['connected'] and report['real_final_tracks'] and report['headings_outside'],report
        if case=='zero-neck':assert report['zero_neck_source_retained'],report
        else:assert report['selected_inward_necks'],report
        if case=='via':assert report['via_ports']>0,report
        if case=='contract':assert all(report['contracts'].values()) and report['via_ports']>0,report
