import json,os,subprocess
from pathlib import Path
import pytest

def test_same_head_priority_changes_only_native_feeder_order(tmp_path):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1]
    process=subprocess.run([interpreter,'-B',str(root/'tests/native_conditional_priority_probe.py'),str(tmp_path)],
        cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=70)
    assert process.returncode==0,process.stdout+process.stderr
    result=json.loads((tmp_path/'report.json').read_text())
    assert result['passed'] and result['caller_unchanged'] and result['model_unchanged']
    assert result['priority']['accepted'] and not result['full_matrix_accepted']
