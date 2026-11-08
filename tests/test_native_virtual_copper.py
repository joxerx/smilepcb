import json,os,subprocess
from pathlib import Path
import pytest

def test_virtual_tracks_vias_and_rollback_change_only_the_obstacle_index(tmp_path):
    native=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not native:pytest.skip('Select Native KiCad')
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run([native,str(Path(__file__).with_name('native_virtual_copper_probe.py')),str(tmp_path)],env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+run.stderr
    q=json.loads((tmp_path/'result.json').read_text())
    assert all(q[k] for k in ['clear_before','blocks_foreign','allows_own','via_on_back','unchanged_during','unchanged_after','clear_restored']),q
    assert q['operations_after_restore']==0
    assert q['loop_safe'],q
    assert q['filter_safe'] and False in q['admissions'] and True in q['admissions'],q
    assert q['pad_join_allowed'],q
