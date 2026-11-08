"""The optional continuation predicate participates in physical goal admission."""
import json,os,subprocess
from pathlib import Path
import pytest


def test_continuation_keyword_preserves_default_and_honors_predicate(tmp_path):
    native=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not native:pytest.skip('Select Native KiCad')
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run([native,str(Path(__file__).with_name('native_obstacle_continuation_probe.py')),str(tmp_path)],env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+run.stderr
    report=json.loads((tmp_path/'result.json').read_text())
    assert report['legacy-default']['connected']
    assert report['callback-accepts']['connected'] and report['callback-accepts']['predicate_calls']>0
    assert not report['callback-vetoes']['connected'] and report['callback-vetoes']['emitted_operations']==0
    assert report['callback-vetoes']['failures']['continuation-self-contact']>0
    assert all(row['native_board_unchanged'] for row in report.values())
