"""Exercise real native pair admission instead of a mock collision oracle."""
import json,os,subprocess
from pathlib import Path
import pytest


def test_native_joint_pair_admission_and_transaction(tmp_path):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for native pair checks')
 root=Path(__file__).resolve().parents[1]
 run=subprocess.run([interpreter,str(Path(__file__).with_name('native_joint_pair_probe.py')),str(tmp_path)],cwd=root,
                    env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=60)
 assert run.returncode==0,run.stdout+'\n'+run.stderr
 assert json.loads((tmp_path/'report.json').read_text())==dict.fromkeys(
  ('legal_narrow_pair','originals_preserved','connected','octile','repeat_unchanged','blocked_rejected','blocked_unchanged'),True)
