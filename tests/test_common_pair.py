"""Actual Native admission of a common pair between perpendicular banks."""
import json,os,subprocess
from pathlib import Path
import pytest


def test_native_common_pair_admission_and_rollback(tmp_path):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for native pair checks')
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_common_pair_probe.py')),str(tmp_path)],cwd=root,
        env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=60)
    assert run.returncode==0,run.stdout+'\n'+run.stderr
    report=json.loads((tmp_path/'report.json').read_text())
    assert report and all(report.values()),report
