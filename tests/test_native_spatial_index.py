import json,os,subprocess
from pathlib import Path
import pytest

def test_finer_spatial_index_preserves_native_collision_answers_and_reduces_work(tmp_path):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path/'result.json'
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_spatial_index_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=20)
 assert r.returncode==0,r.stdout+r.stderr
 d=json.loads(out.read_text());old,new=d['4000000'],d['1000000']
 assert old['answers']==new['answers'] and d['source_exact'],d
 assert new['native_shape_tests']<old['native_shape_tests']/2,(old['native_shape_tests'],new['native_shape_tests'])
