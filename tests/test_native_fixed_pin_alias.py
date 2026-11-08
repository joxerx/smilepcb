import json,os,subprocess
from pathlib import Path
import pytest
import sexpdata

def test_physical_aliases_match_real_kicad_front_back_and_cardinal_rotations(tmp_path):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path/'native-alias'
 env={**os.environ,'PYTHONPATH':os.pathsep.join((str(root),str(Path(sexpdata.__file__).parent))),'PYTHONDONTWRITEBYTECODE':'1'}
 done=subprocess.run([interpreter,'-B',str(Path(__file__).with_name('native_fixed_pin_alias_probe.py')),str(out)],cwd=root,env=env,capture_output=True,text=True,timeout=20)
 assert done.returncode==0,done.stdout+done.stderr
 rows=json.loads((out/'result.json').read_text())
 assert {q['case'] for q in rows}=={'front-0','front-90','front-180','front-270','back-0','back-90','back-180','back-270'}
 assert all(q['passed'] for q in rows)
