import json,os,subprocess
from pathlib import Path
import pytest


def test_full_and_relaxed_repair_oracles_share_native_closed_fixed_lifecycle(tmp_path):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([interpreter,str(Path(__file__).with_name('native_repair_fixed_parity_probe.py')),str(tmp_path)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=55)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads((tmp_path/'report.json').read_text())
    assert report['native_corridor_clear'] and report['fixed_native_complete'] and report['fixed_copper_exact'],report
    assert report['checks'] and all(q.get('full_before_route_clear') and q.get('relaxed_after_native_refresh_clear') and q.get('lifecycle_equal_after_native_join') for q in report['checks']),report
