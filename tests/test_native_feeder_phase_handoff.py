import json,os,subprocess
from pathlib import Path
import pytest


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('feeder-phase-handoff')
    result=subprocess.run([interpreter,str(Path(__file__).with_name('native_feeder_phase_handoff_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=100)
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads((out/'report.json').read_text())


@pytest.mark.parametrize('case',['back-subwindow','front-subwindow'])
def test_actual_final_fine_join_gets_a_fresh_incumbent_ladder_within_original_stop(report,case):
    result=report[case];assert result['native_fine_join'],result
    assert not result['pending'] and not result['saved_pending'] and result['history'][-1]['accepted'],result
    incumbents=[q for q in result['calls'] if 'inner' not in q['nets']]
    assert incumbents and incumbents[0]['grid_nm']==500000 and all(q['router'] not in result['expired_routers'] for q in incumbents),result
    assert all(result[k] for k in ('all_requests_within_original_stop','source_field_live_exact','metadata_exact','scope_valid','contacts_preserved')),result
    assert result['history'][-1]['local_copper_exact'] and result['history'][-1]['retained_contacts_preserved'],result


@pytest.mark.parametrize('case',['back-global','front-global'])
def test_actual_fine_join_cannot_renew_an_expired_transaction_and_rolls_back_exactly(report,case):
    result=report[case];assert result['native_fine_join'],result
    assert result['exact'] and result['scope_exact'] and result['pending']=={'inner':1},result
    assert not any('inner' not in q['nets'] for q in result['calls']),result
    assert all(result[k] for k in ('all_requests_within_original_stop','source_field_live_exact','metadata_exact','contacts_preserved')),result
