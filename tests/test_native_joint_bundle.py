import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('joint-bundle')
 done=subprocess.run([interpreter,str(Path(__file__).with_name('native_joint_bundle_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=70)
 assert done.returncode==0,done.stdout+done.stderr
 data=json.loads((out/'result.json').read_text());data['_native_stderr']=done.stderr;return data
def test_joint_rewrite_straightens_a_pair_whose_individual_shortcuts_are_blocked(report):
 assert 'joint' in report,report
 r=report['joint'];assert r['report']['accepted'] and r['report']['changed_groups']>0,r
 assert r['report']['after_metrics']['bends']<r['report']['before_metrics']['bends']
 assert r['contacts_exact'] and r['footprints_exact'] and r['paths_valid'],r
def test_only_deferred_fill_can_retreat_during_joint_rewrite(report):
 assert 'fixed-fill' in report,report
 assert report['fixed-fill']['report']['changed_groups']<=report['joint']['report']['changed_groups']
def test_expired_joint_pass_retains_the_exact_complete_incumbent(report):
 assert 'expired' in report and report['expired']['unchanged'],report
def test_source_locks_and_ground_pad_contacts_survive_joint_pass(report):
 assert 'locked' in report,report
 assert report['locked']['unchanged'] and report['locked']['locked_exact']
 assert all(report[k]['contacts_exact'] for k in ('joint','fixed-fill','expired','locked'))

def test_native_ground_island_created_by_a_proposed_shortcut_rolls_back_every_change(report):
 assert 'ground-cut' in report,report
 r=report['ground-cut']
 assert r['report']['accepted'] and r['report']['changed_groups']==0 and r['unchanged'] and r['contacts_exact'],r
 assert any('contacts' in q.get('rejection_reason','').lower() for q in r['report']['attempts']),r

def test_joint_proposal_cannot_add_a_loop_through_retained_same_net_copper(report):
 r=report.get('retained-loop',{})
 assert r.get('new_contact_rejected') and r.get('endpoint_contact_allowed') and r.get('unchanged'),r

def test_intended_via_terminal_uses_its_actual_layer_diameter(report):
 r=report.get('via-endpoint',{})
 assert r.get('actual_diameter_nm')==500000 and r.get('allowed'),r

def test_via_contact_query_does_not_trigger_a_kicad_api_assertion(report):
 assert 'PCB_VIA::GetWidth called without a layer argument' not in report['_native_stderr']

def test_single_chain_rejection_keeps_an_earlier_valid_rewrite(report):
 r=report.get('partial-single',{})
 assert r.get('contacts_exact') and r.get('improved') and r.get('report',{}).get('accepted'),r
 assert any(q.get('adopted') and q['net']=='third' for q in r['report']['rewrites']),r
