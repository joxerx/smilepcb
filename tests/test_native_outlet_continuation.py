import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('case',('search-east','search-west','search-north','search-south','search-wide','search-via',
                                'proof-short','proof-wide','proof-fixed','proof-long'))
def test_native_outlet_is_consumable_under_its_own_first_contact_policy(tmp_path,case):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path/'report.json'
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_outlet_continuation_probe.py')),case,str(out)],
        cwd=root,env={**os.environ,'PYTHONPATH':str(root),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=15)
    assert run.returncode==0,run.stdout+run.stderr
    report=json.loads(out.read_text())
    assert report['connected'],report
    if case in ('proof-short','proof-wide','proof-fixed'):
        assert report['continuation_collisions']['200000']==['controller-first-contact'],report
        assert report['proof_error'] is not None,report
    else:
        assert report['continuation_collisions']=={'150000':[],'200000':[]},report
        assert report['proof_error'] is None,report
        if case=='search-via':assert len(report['plan']['vias'])==1,report
