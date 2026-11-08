import json,os,subprocess
from pathlib import Path
import pytest

def test_short_shared_budget_can_complete_a_real_native_feeder_repair(tmp_path):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path/'result.json'
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_short_feeder_budget_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=20)
 assert r.returncode==0,r.stdout+r.stderr
 d=json.loads(out.read_text());assert not d['missing'] and d['footprints_exact'],d
 assert any(q['accepted'] and q['contacts_preserved'] for q in d['history']),d
