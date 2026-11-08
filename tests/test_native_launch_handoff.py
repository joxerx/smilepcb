import json,os,subprocess
from pathlib import Path
import pytest

def test_verified_full_matrix_handoff_supersedes_retired_launch_receipt(tmp_path):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];r=subprocess.run([interpreter,str(Path(__file__).with_name('native_launch_handoff_probe.py')),str(tmp_path)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
 assert r.returncode==0,r.stdout+r.stderr
 report=json.loads((tmp_path/'report.json').read_text())
 assert report['native_handoff_valid'] and report['worker_accepts_authorized_retirement'],report
 assert report['invalid_handoff_rejected'] and report['board_exact'],report
