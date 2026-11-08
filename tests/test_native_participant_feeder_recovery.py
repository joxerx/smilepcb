import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select Native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('participant-feeder-recovery');env={**os.environ,'PYTHONPATH':os.pathsep.join(filter(None,(str(root),os.environ.get('PYTHONPATH'))))}
 run=subprocess.run([interpreter,'-X','faulthandler','-B',str(Path(__file__).with_name('native_participant_feeder_recovery_probe.py')),str(out)],cwd=root,env=env,capture_output=True,text=True,timeout=90)
 assert run.returncode==0,run.stdout+run.stderr
 return json.loads((out/'report.json').read_text())
@pytest.mark.parametrize('case',['back','front-shifted'])
def test_real_mixed_return_and_matrix_recovery_preserves_separate_old_return_island(report,case):
 r=report[case];assert r['accepted'] and r['missing']=={},r
 assert r['prior_contacts_preserved'] and r['hard_exact'] and r['footprints_exact'] and r['cold_exact'],r
 assert r['return_partitions']==2 and r['return_participant_displaced'],r
