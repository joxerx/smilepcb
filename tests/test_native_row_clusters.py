import json,os,subprocess
from pathlib import Path
import pytest

@pytest.mark.parametrize('case',['connected','singleton','failure','expiry','exception','global-expiry','hygiene','global-metadata','retained-connection','field-exception'])
def test_native_cluster_spines_links_and_atomic_rollback(tmp_path,case):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for actual native clusters')
    root=Path(__file__).resolve().parents[1];output=tmp_path/'result.json'
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_row_clusters_probe.py')),case,str(output)],
        cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=35)
    assert run.returncode==0,run.stdout+run.stderr
    assert json.loads(output.read_text())
