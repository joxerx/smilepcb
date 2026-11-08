import json,os,subprocess
from pathlib import Path
import pytest


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('provisional-collector-rollback')
    result=subprocess.run([interpreter,str(Path(__file__).with_name('native_provisional_collector_rollback_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=100)
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads((out/'report.json').read_text())


@pytest.mark.parametrize('case',['timeout-after-negotiation','timeout-during-restoration','incumbent-restore-fails'])
def test_rejected_real_provisional_repair_restores_copper_contacts_and_source_scope(report,case):
    result=report[case]
    assert result['mutated_native_candidate'] and all(result['mutated_native_candidate']),result
    assert result['exact'] and result['metadata_exact'] and result['scope_exact'],result
    assert result['pending']==result['initial'] and not result['history'][-1]['accepted'],result


def test_fewer_total_gaps_cannot_split_an_incumbent_closed_collector(report):
    result=report['incumbent-restore-fails'];last=result['history'][-1]
    assert sum(last['after'].values())<sum(last['before'].values()),result
    assert not last['contacts_preserved'] and not last['accepted'],result


@pytest.mark.parametrize('case',['missing-live-advance','omitted-physical-alias'])
def test_invalid_original_or_live_physical_source_inventory_fails_before_mutation(report,case):
    result=report[case]
    assert result['error'] and result['exact'],result


def test_expired_planning_window_still_validates_real_carve_and_restores_within_transaction_reserve(report):
    result=report['plan-window-expired']
    assert result['plan_expired'] and len(set(result['plan_deadlines']))==1,result
    assert result['boundary_events'][-1]['newly_reopened']==[] and any(q['newly_reopened'] for q in result['boundary_events']),result
    assert result['restored_after_expiry'] and all(q['now']>result['plan_deadlines'][0] and q['now']<q['deadline'] for q in result['restored_after_expiry']),result
    assert not result['pending'] and result['history'][-1]['accepted'],result
    assert result['metadata_exact'] and result['history'][-1]['protected_exact'] and result['history'][-1]['contacts_preserved'],result
