import json,os,subprocess
from pathlib import Path
import pytest
@pytest.mark.parametrize('case',['owner','foreign'])
def test_interface_launch_uses_both_nets_aggregate_native_clearance(tmp_path,case):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];r=subprocess.run([interpreter,str(Path(__file__).with_name('native_launch_clearance_probe.py')),str(tmp_path)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
 assert r.returncode==0,r.stdout+r.stderr
 row=json.loads((tmp_path/'report.json').read_text())[case];assert all(row.values()),row
