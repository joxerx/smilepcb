import json,os,subprocess
from pathlib import Path
import pytest

@pytest.mark.parametrize('case',['connected','late-failure','late-exception'])
def test_native_row_branch_batch_index_and_rollback(tmp_path,case):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for actual native row attachments')
    root=Path(__file__).resolve().parents[1];output=tmp_path/'result.json'
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_row_branch_batch_probe.py')),case,str(output)],
        cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+run.stderr
    assert json.loads(output.read_text())['launch_inventory_exact']
