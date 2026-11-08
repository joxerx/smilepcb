import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for actual native handoff')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('field-handoff')
    r=subprocess.run([interpreter,str(Path(__file__).with_name('native_field_handoff_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=45)
    assert r.returncode==0,r.stdout+r.stderr
    return json.loads((out/'report.json').read_text())

@pytest.mark.parametrize('name',['damaging_pass_rejected','saved_board_rolled_back','addition_preserves_field','broken_access_rejected','all_physical_aliases_recorded','rollback_post-save-proof','rollback_receipt-write','inline_row_bridges_are_structural'])
def test_matrix_worker_boundary_uses_actual_copper(report,name):
    assert report[name] is True,report
