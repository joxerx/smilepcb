"""Real Native coverage of completed-candidate collection and retained best."""
import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
    native=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not native:pytest.skip('Select Native KiCad')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('complete-choice')
    run=subprocess.run([native,str(Path(__file__).with_name('native_complete_choice_probe.py')),str(out)],
        env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=70)
    assert run.returncode==0,run.stdout+run.stderr
    return json.loads((out/'result.json').read_text())

def test_several_complete_native_candidates_compete_before_mutation(report):
    r=report['best'];s=r['plan']['selection']
    assert s['completed_unique']>=2,r
    assert r['readonly'] and r['result']['accepted'],r
    assert r['contacts_exact'] and r['footprints_exact'],r
    selected=s['candidates'][s['selected_index']]
    assert selected['score']==max(c['score'] for c in s['candidates'] if c['eligible'])
    m=r['final_metrics']
    assert selected['score']==-m['length_nm']-m['bends']*500000-m['vias']*1000000

def test_native_timeout_keeps_and_can_commit_already_complete_best(report):
    r=report['timeout-after-complete']
    assert r['score_calls']>=2 and r['plan']['selection']['completed_unique']>=1,r
    assert r['plan']['failures']['collection-timeout']>0,r
    assert r['readonly'] and r['result']['accepted'] and r['contacts_exact'],r

def test_no_quality_eligible_completed_native_candidate_preserves_source(report):
    r=report['ineligible']
    assert r['plan']['selection']['completed_unique']>=1,r
    assert r['plan']['selection']['eligible_unique']==0,r
    assert not r['plan']['planned'] and r['readonly'] and r['unchanged'],r
