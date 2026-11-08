"""Run focused native goals checks only when the KiCad token is available."""
import json,os,subprocess
from pathlib import Path
import pytest


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('alternative-goals')/'report.json'
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_collector_alternative_goals_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=60)
    assert run.returncode==0,run.stdout+run.stderr
    return json.loads(out.read_text())


@pytest.mark.parametrize('face',[0,2])
def test_clear_farther_field_contact_follows_blocked_nearest_on_the_same_frontier(report,face):
    r=report['wall-'+str(face)]
    assert r['result']['accepted'] and r['connected'] and r['source_exact'] and r['ports_exact'],r
    assert r['rollback_exact'] and all(r['rollback_exact']),r
    assert r['attempts'][0]!=r['branch']['target_nm'],r
    assert r['result']['stats']['native_goal_rejections']<12,r


def test_postmutation_proof_rejection_rolls_back_before_next_goal(report):
    r=report['atomic'];assert r['injected_after_mutation']==[True],r
    assert r['rollback_exact'] and all(r['rollback_exact']),r
    assert r['result']['accepted'] and r['connected'] and r['ports_exact'],r


def test_expired_alternative_search_preserves_exact_sources(report):
    r=report['expired'];assert not r['result']['accepted'] and r['exact'],r


def test_route_collectors_closes_the_winning_alternative_net_after_full_search(report):
    r=report['integration'];assert r['result']['accepted'] and r['connected'] and r['source_exact'],r
    assert r['result']['closed_nets']==['inner','outer'],r
    accepted=[q for q in r['result']['history'] if q.get('kind')=='peel' and q.get('accepted')]
    assert accepted and accepted[0]['branch']['net']=='inner',r
    assert accepted[0]['branch']['target_nm']==[20000000,27000000],accepted
    assert all(q['branch']['net'] in r['result']['closed_nets'] for q in accepted),accepted


def test_same_point_alternate_face_uses_the_actual_plated_pad_witness(report):
    r=report['faces'];assert r['result']['accepted'] and r['connected'] and r['source_exact'],r
    assert r['branch']['field_contact_id']==r['expected_contact_id'] and r['branch']['via_nm'] is None,r
    assert r['result']['stats']['goals_admitted']==1,r


def test_axis_excludes_its_own_scope_but_keeps_unrelated_fixed_departures(report):
    r=report['scope'];assert r['cohort_axis_clear'] and r['fixed_axis_blocked'] and r['exact'],r
    assert any(key=='reserved-controller-launch' for key,count in r['blockers']),r


def test_shared_deadline_expiring_after_branch_mutation_restores_every_source(report):
    r=report['mid-expired'];assert r['proof_after_mutation']==[True],r
    assert not r['result']['accepted'] and r['exact'] and r['branch_clear'],r
