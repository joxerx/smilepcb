import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select pinned native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('via-junction')
 done=subprocess.run([interpreter,str(Path(__file__).with_name('native_via_junction_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},text=True,capture_output=True,timeout=75)
 assert done.returncode==0,done.stdout+done.stderr
 data=json.loads((out/'result.json').read_text())
 return data

def get_case(report,name):
 assert name in report,report
 return report[name]

def test_moves_both_face_arms_and_removes_old_detour(report):
 r=get_case(report,'movable');assert r['report']['accepted'] and r['report']['moved_vias']>=1,r
 assert r['after_metrics']['bends']<r['before_metrics']['bends']
 assert r['after_metrics']['length_nm']<r['before_metrics']['length_nm']
 assert r['contacts_exact'] and r['saved_exact'] and r['footprints_exact']
 assert r['via_identity_exact'] and r['via_width_nm']==700000 and r['via_drill_nm']==300000

def test_third_incident_arm_remains_connected(report):
 r=get_case(report,'third-arm');assert r['report']['moved_vias']>=1 and r['contacts_exact'] and r['saved_exact'],r
 assert r['after_metrics']['bends']<r['before_metrics']['bends']

@pytest.mark.parametrize('case',['locked-via','locked-arm','expired'])
def test_lock_and_budget_keep_complete_incumbent(report,case):
 r=get_case(report,case);assert r['unchanged'] and r['contacts_exact'] and r['saved_exact'],r

def test_ground_cut_rejects_and_restores_all_copper(report):
 r=get_case(report,'ground-rollback');assert not r['report']['accepted'],r
 assert 'contact' in r['report']['reason'].lower(),r
 assert r['unchanged'] and r['contacts_exact'],r

def test_rechecks_locks_when_applying_previously_discovered_star(report):
 r=get_case(report,'late-lock');assert not r['report']['accepted'] and r['unchanged'],r


def test_partial_native_emission_exception_restores_exact_source(report):
 r=get_case(report,'partial-emission');assert r['unchanged'] and r['contacts_exact'] and r['fault_type']=='RuntimeError',r

def test_implicit_old_mid_segment_branch_is_not_detached(report):
 r=get_case(report,'unmodeled-branch');assert r['star_count']==0,r

def test_copper_capsule_cannot_graze_same_net_pad_to_add_a_loop(report):
 r=get_case(report,'pad-graze');assert not r['report']['accepted'] and r['unchanged'],r


def test_emission_before_index_bookkeeping_restores_exact_source(report):
 r=get_case(report,'pre-bookkeeping-emission');assert r['unchanged'] and r['contacts_exact'] and r['fault_type']=='RuntimeError',r

def test_junction_search_and_direct_transaction_cannot_bypass_area_quality(report):
 a=get_case(report,'quality-veto-search');b=get_case(report,'quality-veto-direct')
 assert a['unchanged'] and a['report'].get('moved_vias')==0 and 'fault' not in a['report'],a
 assert b['unchanged'] and b['report'].get('accepted') is False and 'fault' not in b['report'],b
