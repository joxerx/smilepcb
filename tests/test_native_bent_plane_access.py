import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('bent-plane-access');r=subprocess.run([interpreter,str(Path(__file__).with_name('native_bent_plane_access_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'report.json').read_text())

def test_return_access_uses_a_native_clear_bent_lead_when_all_straight_leads_fail(report):
 r=report['bend'];assert r['accepted'] and r['bent'] and r['pad_reaches_real_via'],r
 assert r['native_clear'] and r['source_exact'],r

def test_return_access_still_rejects_a_real_closed_barrier_and_rolls_back(report):
 r=report['blocked'];assert not r['accepted'] and r['rollback_exact'] and r['source_exact'],r

def test_return_access_does_not_exceed_its_local_lead_length_budget(report):
 r=report['length_bound'];assert not r['accepted'] and r['rollback_exact'] and r['source_exact'],r

def test_controller_return_grid_join_reaches_existing_via_without_an_extra_hole(report):
 r=report['grid_join'];assert r['accepted'] and r['pad_reaches_existing_via'] and r['new_tracks']>0,r
 assert r['no_extra_via'] and r['native_clear'] and r['source_exact'],r

def test_controller_return_grid_join_cannot_bypass_a_real_barrier(report):
 r=report['grid_blocked'];assert not r['accepted'] and not r['pad_reaches_existing_via'] and r['rollback_exact'],r

def test_controller_return_grid_join_checks_the_wider_back_annulus_against_solder_lands(report):
 r=report['grid_wide_annulus'];assert not r['accepted'] and r['rollback_exact'],r

def test_return_access_prefers_an_available_straight_lead_over_an_earlier_bent_proposal(report):
 r=report['straight_priority'];assert r['accepted'] and r['track_count']==1 and r['native_clear'] and r['source_exact'],r


def test_grid_fallback_copper_is_visible_to_the_shared_foreign_clearance_oracle(report):
 r=report['grid_join'];assert r['accepted'] and r['fallback_indexed'],r
