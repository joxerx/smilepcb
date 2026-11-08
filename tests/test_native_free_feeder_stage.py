"""The real fresh-bank caller invokes actual-pad fallback, not a saved PCB import."""
import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    import sexpdata
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('free-feeder-stage')
    env={**os.environ,'PYTHONPATH':os.pathsep.join((str(root),str(Path(sexpdata.__file__).parent),os.environ.get('PYTHONPATH','')))}
    process=subprocess.run([interpreter,'-B',str(root/'tests/native_free_feeder_stage_probe.py'),str(out)],cwd=root,env=env,capture_output=True,text=True,timeout=180)
    assert process.returncode==0,process.stdout+process.stderr
    return json.loads((out/'report.json').read_text())

@pytest.mark.parametrize('face',['front','back'])
def test_fresh_native_bank_caller_finishes_actual_pad_fallback(report,face):
    row=report[face]
    assert row['result']['accepted'] and row['result']['source_mode']=='native_pads'
    assert row['result']['free_feeders']['saved_reload_exact']
    assert all(row[k] for k in ('model_unchanged','scope_unchanged','field_exact','ordered_cutoff_reserved'))

@pytest.fixture(scope='module')
def partial_seed_report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    import sexpdata
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('free-feeder-partial-seed')
    env={**os.environ,'PYTHONPATH':os.pathsep.join((str(root),str(Path(sexpdata.__file__).parent),os.environ.get('PYTHONPATH','')))}
    process=subprocess.run([interpreter,'-B',str(root/'tests/native_free_feeder_stage_probe.py'),str(out),'--partial-seed'],
        cwd=root,env=env,capture_output=True,text=True,timeout=180)
    assert process.returncode==0,process.stdout+process.stderr
    return json.loads((out/'report.json').read_text())['front']

def test_rejected_native_bank_reuses_its_closed_partial_seed(partial_seed_report):
    row=partial_seed_report;case=row['partial_seed_case'];fallback=row['result']['free_feeders']
    assert row['result']['accepted'] and not case['attempt_accepted']
    assert len(case['missing'])==1 and len(case['closed_nets'])==1
    assert case['partial_seed_nets']==case['closed_nets']
    assert case['seed_copper_count']>0
    assert case['scratch_head_check']==[True]
    assert fallback['native_proof']['accepted'] and fallback['reload_proof']['accepted']
    assert all(row[k] for k in ('model_unchanged','scope_unchanged','field_exact'))
