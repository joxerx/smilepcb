import json,os,subprocess
from pathlib import Path
import pytest

def test_almost_complete_matrix_borrows_time_before_discarding_its_sources(tmp_path):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path/'result.json'
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_matrix_budget_borrow_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=80)
 assert r.returncode==0,r.stdout+r.stderr
 d=json.loads(out.read_text());assert not d['False']['complete'] and d['False']['pending'],d
 assert d['True']['complete'] and d['True']['proof'] and d['True']['repair_calls']==2,d
 assert d['True']['later_window'] and d['True']['within_outer'] and d['True']['footprints_exact'],d
