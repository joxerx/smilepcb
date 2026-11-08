import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('session-contacts')
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_session_contacts_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=50)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'contacts-report.json').read_text())

def test_import_cannot_exchange_existing_contact_for_another_with_equal_gap_count(report):
 r=report['split'];assert r['exit_code']!=0 and r['source_unchanged'] and r['before_groups']==r['after_groups']==3,r

def test_import_can_merge_previously_separate_pad_groups(report):
 r=report['merge'];assert r['exit_code']==0 and r['before_groups']==3 and r['after_groups']==1,r
