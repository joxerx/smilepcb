import json,os,subprocess
from pathlib import Path
import pytest
import sexpdata

@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('post-matrix-interface')
    command=[interpreter,str(Path(__file__).with_name('native_post_matrix_interface_scope_probe.py')),str(out)]
    env={**os.environ,'PYTHONPATH':os.pathsep.join((str(root),str(Path(sexpdata.__file__).parent)))}
    prepared=subprocess.run([*command,'--prepare'],cwd=root,env=env,capture_output=True,text=True,timeout=15)
    assert prepared.returncode==0,prepared.stdout+prepared.stderr
    run=subprocess.run(command,cwd=root,env=env,capture_output=True,text=True,timeout=45)
    assert run.returncode==0,run.stdout+run.stderr
    return json.loads((out/'report.json').read_text())

@pytest.mark.parametrize('face',['front','back'])
def test_open_control_runway_and_direct_first_contact_survive_full_matrix_handoff(report,face):
    assert report[face]['native_full_matrix']
    assert all(report[face]['old_control'].values()),report[face]['old_control']
    assert all(report[face]['live'].values()),report[face]['live']
    assert all(report[face]['components'].values()),report[face]['components']

@pytest.mark.parametrize('face',['front','back'])
def test_only_current_full_numbered_pad_closure_retires_a_stale_head(report,face):
    assert report[face]['all_native_completed_retired']
    assert report[face]['strict_capture_unchanged'] and report[face]['reopened_missing_head_rejected']

@pytest.mark.parametrize('face',['front','back'])
@pytest.mark.parametrize('case',['missing_live_head','deadline','invalid_matrix'])
def test_invalid_or_expired_handoff_fails_without_changing_saved_source(report,face,case):
    assert report[face][case]=={'rejected':True,'saved_source_exact':True}
    assert report[face]['scratch_restored']


@pytest.mark.parametrize('face',['front','back'])
@pytest.mark.parametrize('case',['intact','moved','retired','raw_bad','raw_good','closed_bad','closed_good','closed_subset','source_mutation','deadline'])
def test_actual_reopen_and_all_backend_delta_boundaries(report,face,case):
    assert all(report[face]['extra'][case].values()),report[face]['extra'][case]
