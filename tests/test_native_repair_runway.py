"""Independent native full-handoff repair-only runway controls."""
import json,os,subprocess
from pathlib import Path
import pytest
import sexpdata

@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];runtime=Path(os.environ.get('SMILEPCB_CONTROL_RUNTIME',str(root)));out=tmp_path_factory.mktemp('repair-runway')
    env={**os.environ,'PYTHONPATH':os.pathsep.join((str(runtime),str(Path(sexpdata.__file__).parent)))}
    prepare=subprocess.run([interpreter,str(Path(__file__).with_name('native_repair_runway_probe.py')),str(out),'--prepare'],cwd=root,env=env,capture_output=True,text=True,timeout=10)
    assert prepare.returncode==0,prepare.stdout+prepare.stderr
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_repair_runway_probe.py')),str(out)],cwd=root,env=env,capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+run.stderr
    return json.loads((out/'report.json').read_text())

@pytest.mark.parametrize('face',['front','back'])
@pytest.mark.parametrize('case',['entry','normal_track','normal_via','raw_track','raw_via','same_net','field_branch','other_face','ordinary_components','ordinary_cleanup','nested_and_effective','exit'])
def test_actual_repair_only_reservation_keeps_native_truth_and_atomicity(report,face,case):
    assert all(value is True for value in report['faces'][face][case].values()),report['faces'][face][case]
