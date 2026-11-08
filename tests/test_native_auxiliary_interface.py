import json,os,subprocess
from pathlib import Path
import pytest

def test_ordered_handoff_reuses_plane_returns_and_allows_direct_local_control_access(tmp_path):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path/'aux'
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_auxiliary_interface_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=35)
 assert r.returncode==0,r.stdout+r.stderr
 proof=json.loads((out/'report.json').read_text());assert proof['accepted'],proof
 ports=proof['ports']['ports'];boot=next(q for q in ports if q['net']=='BOOT0')
 assert boot['kind']=='same_layer' and not ({q['net'] for q in ports}&{'+3V3','GND'}),proof
