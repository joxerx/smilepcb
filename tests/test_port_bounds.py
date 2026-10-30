"""Native broad-phase equivalence for immutable planned copper and drill shapes."""
import json,os,subprocess
from pathlib import Path
import pytest


def test_port_bounds_keep_native_contact_semantics(tmp_path):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for native port bounds')
 root=Path(__file__).resolve().parents[1];out=tmp_path/'report.json'
 run=subprocess.run([interpreter,str(Path(__file__).with_name('native_port_bounds_probe.py')),str(out)],cwd=root,
                    env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
 assert run.returncode==0,run.stdout+'\n'+run.stderr
 assert json.loads(out.read_text())=={'checked':294,'native_equivalent':True}
