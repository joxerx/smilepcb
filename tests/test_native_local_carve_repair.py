import json,os,subprocess
from pathlib import Path
import pytest


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('local-carve-repair')
    result=subprocess.run([interpreter,str(Path(__file__).with_name('native_local_carve_repair_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=100)
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads((out/'report.json').read_text())


@pytest.mark.parametrize('case',['back-long-straight','front-long-dogleg'])
def test_native_local_repair_closes_pending_and_incumbents_without_retiring_clear_feeder_sections(report,case):
    result=report[case]
    assert not result['missing'] and not result['saved_missing'],result
    assert result['remote_piece_count']>=4 and result['remote_exact'] and result['unaffected_old_exact'] and result['retained_fragments_exact'],result
    assert any(q['accepted'] for q in result['history']),result
    assert all(result[k] for k in ('scope_valid','all_pads_retained','source_and_live_exact','contacts_preserved','input_metadata_exact','fragment_ancestry_valid')),result


@pytest.mark.parametrize('case',['back-long-straight','front-long-dogleg'])
def test_native_original_missing_ladder_precedes_a_separate_incumbent_ladder(report,case):
    calls=report[case]['restoration_calls']
    assert calls and calls[0]['nets']==report[case]['original_missing_nets'],report[case]
    index=next((i for i,call in enumerate(calls) if call['nets']==report[case]['displaced_nets']),None)
    assert index is not None and index>0,report[case]
    first,restore=calls[index-1:index+1]
    assert first['nets']==report[case]['original_missing_nets'] and first['router']!=restore['router'] and restore['grid_nm']==calls[0]['grid_nm'],calls

    assert len(report[case]['planning_deadlines'])>=2 and len(set(report[case]['planning_deadlines']))==1,report[case]
