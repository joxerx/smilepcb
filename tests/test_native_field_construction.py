import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for native field construction')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('field-construction');r=subprocess.run([interpreter,str(Path(__file__).with_name('native_field_construction_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'report.json').read_text())

@pytest.mark.parametrize('name',['field_complete','offset_diodes_do_not_hide_the_key_row','row_stays_on_preferred_face','source_preserved','expired_adds_nothing'])
def test_actual_field_contacts_and_straight_channel_admission(report,name):
 assert report[name] is True,report

def test_all_physical_pin_aliases_are_checked(report):
 assert report['actual_pad_count']==6
