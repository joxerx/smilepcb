import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('collector-review')/'report.json'
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_collector_review_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=100)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads(out.read_text())
def test_selected_process_rules_govern_native_via_planning(report):
 assert report['profile']=={'diameter':500000,'drill':200000}
def test_final_receipt_timeout_rejects_pose_and_rolls_back(report):
 r=report['late-proof'];assert not r['accepted'] and r['unchanged'] and not r['has_receipt'],r
def test_fallback_and_launch_guards_use_advanced_unresolved_sources(report):
 r=report['live-ports'];assert r['advanced'] and r['observations'],r
 assert all(q['policy'] and q['launch'] for q in r['observations']),r


def test_expired_feeder_repair_retains_incumbent_without_starting_native_proof(report):
 r=report['expired-repair'];assert r['error'] is None and r['history']==[] and r['unchanged'],r
