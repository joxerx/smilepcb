"""No fake physical clears: each case runs in a separate native process."""
import json,os,subprocess
from pathlib import Path
import pytest

@pytest.mark.parametrize('name',['foreign-npth','edge-cut-slot','concave-outline',
    'opposite-face-bridge','same-net-plated','annulus-only'])
def test_straight_projection_rejects_only_shared_physical_barriers(tmp_path,name):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for native barrier checks')
    root=Path(__file__).resolve().parents[1];output=tmp_path/'report.json'
    command=[interpreter,str(Path(__file__).with_name('native_field_barrier_probe.py')),
        '--case',name,'--output',str(output)]
    result=subprocess.run(command,cwd=root,env={**os.environ,'PYTHONPATH':str(root)},
        capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads(output.read_text())
    assert report['passed'],report
