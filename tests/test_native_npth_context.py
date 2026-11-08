import json,os,subprocess
from pathlib import Path
import pytest

def test_fresh_and_reloaded_npth_keep_declared_hole_floor(tmp_path):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([interpreter,'-B',str(root/'tests/native_npth_context_probe.py'),str(tmp_path)],
        cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    rows=json.loads((tmp_path/'report.json').read_text())
    assert len(rows)==2
    for row in rows:
        assert row['fresh']['npth']['effective_nm']==row['reloaded']['npth']['effective_nm']==row['hole_floor_nm']+1000
        assert row['fresh']['pth']['effective_nm']==row['fresh']['smd']['effective_nm']==151000
        assert row['fresh']['npth-high']['effective_nm']==row['fresh']['smd-local']['effective_nm']==451000
        assert all(all(w.values()) for w in row['witnesses'])
