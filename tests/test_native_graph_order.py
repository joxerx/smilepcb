"""Random native identities and iteration order must not select a different tree."""
import json,os,subprocess
from pathlib import Path
import pytest
import sexpdata

def test_identical_return_geometry_has_same_tree_with_reversed_native_order(tmp_path):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for native order regression')
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_graph_order_probe.py')),str(tmp_path)],cwd=root,env={**os.environ,'PYTHONPATH':os.pathsep.join((str(root),str(Path(sexpdata.__file__).parent)))},capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+'\n'+run.stderr
    data=json.loads((tmp_path/'report.json').read_text())
    assert data['forward']['accepted'] and data['reverse']['accepted']
    assert data['forward']['missing']==data['reverse']['missing']=={}
    assert data['forward']['source_unchanged'] and data['reverse']['source_unchanged']
    assert data['same_copper_geometry'],data
