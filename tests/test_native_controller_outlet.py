import json,os,subprocess
from pathlib import Path
import pytest

def test_joint_controller_ports_use_a_via_instead_of_accepting_an_interior_stub(tmp_path):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path/'result.json'
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_controller_outlet_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=25)
 assert r.returncode==0,r.stdout+r.stderr
 d=json.loads(out.read_text());assert d['False']['accepted'] and not d['False']['outside'],d
 assert d['True']['accepted'] and d['True']['outside'] and d['True']['closed'] and d['True']['source_exact'],d
 assert d['True']['kinds']==['via'],d

 assert d['preflight']['False']['report']['certified_gpios']==['A0'],d
 assert d['preflight']['True']['report']['uncertain_gpios']==['A0'],d
 assert d['preflight']['True']['report']['excluded_gpios']==[],d
 assert all(q['source_exact'] for q in d['preflight'].values())
