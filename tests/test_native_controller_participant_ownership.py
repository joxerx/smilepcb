import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select Native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('controller-participant-ownership');env={**os.environ,'PYTHONPATH':os.pathsep.join(filter(None,(str(root),os.environ.get('PYTHONPATH'))))}
 run=subprocess.run([interpreter,'-X','faulthandler','-B',str(Path(__file__).with_name('native_controller_participant_ownership_probe.py')),str(out)],cwd=root,env=env,capture_output=True,text=True,timeout=60)
 assert run.returncode==0,run.stdout+run.stderr
 return json.loads((out/'report.json').read_text())
@pytest.mark.parametrize('case,required',[('front',2),('back-shifted',29)])
def test_real_native_creators_bind_only_their_actual_controller_copper(report,case,required):
 r=report[case];assert r['return_owned']>0 and r['control_owned']>0 and r['required_return_pads']==required,r
 assert r['unrelated_return_island_preserved'] and r['entry_copper_exact'] and r['footprints_exact'] and r['cold_ownership_exact'],r
