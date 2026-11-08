import json,os,subprocess
from pathlib import Path
import pytest
import sexpdata


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('complex-repair-sources')
    runtime=Path(os.environ.get('SMILEPCB_CONTROL_RUNTIME',str(root)))
    env={**os.environ,'PYTHONPATH':os.pathsep.join((str(runtime),str(Path(sexpdata.__file__).parent)))}
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_complex_repair_source_probe.py')),str(out)],cwd=root,env=env,capture_output=True,text=True,timeout=20)
    assert run.returncode==0,run.stdout+run.stderr
    return json.loads((out/'report.json').read_text())


@pytest.mark.parametrize('face',['front','back'])
@pytest.mark.parametrize('case',['native_entry','hard_and_carve','raw_closed_delete','raw_retired_delete','raw_retired_add','raw_live_change','alias_corruption','ordinary_subset'])
def test_actual_complex_repair_preserves_source_lifecycle_and_atomic_file(report,face,case):
    assert all(report['faces'][face][case].values()),report['faces'][face][case]
    assert report['faces'][face]['scratch_restored']
