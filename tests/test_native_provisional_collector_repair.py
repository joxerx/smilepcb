import json,os,subprocess
from pathlib import Path
import pytest


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('provisional-collector-repair')
    result=subprocess.run([interpreter,str(Path(__file__).with_name('native_provisional_collector_repair_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=100)
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads((out/'report.json').read_text())


@pytest.mark.parametrize('case',['back','front-shifted'])
def test_native_automatic_repair_closes_pinched_sibling_and_restores_displaced_collector(report,case):
    result=report[case]
    assert not result['missing'] and not result['saved_missing'],result
    assert result['closed_feeder_moved'] and any(q['accepted'] for q in result['history']),result
    assert all(result[k] for k in ('incumbent_contacts_preserved','source_and_live_advances_exact','all_physical_pads_retained','original_metadata_exact','current_metadata_exact','scope_valid')),result
