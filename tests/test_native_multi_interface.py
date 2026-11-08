import json,os,subprocess
from pathlib import Path
import pytest

def test_every_interface_of_a_shared_net_keeps_its_own_first_contact_boundary(tmp_path):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];r=subprocess.run([interpreter,str(Path(__file__).with_name('native_multi_interface_probe.py')),str(tmp_path)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
 assert r.returncode==0,r.stdout+r.stderr
 report=json.loads((tmp_path/'report.json').read_text());assert all(report.values()),report
