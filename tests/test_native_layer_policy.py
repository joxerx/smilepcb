import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select Native KiCad')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('layer-policy');r=subprocess.run([interpreter,str(Path(__file__).with_name('native_layer_policy_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=90);assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'result.json').read_text())
def test_smd_pad_access_is_short_and_long_copper_moves_to_preferred_face(report):
 r=report['smd'];assert r['report'].get('adopted_chains',0)>0 and r['length']['0']>=20_000_000 and r['vias']==2 and r['footprints_exact'],r

def test_existing_through_via_terminals_are_reused_without_more_holes(report):
 r=report['existing-vias'];assert r['length']['0']>=22_000_000 and r['vias']==2 and r['old_vias_exact'],r

def test_locked_opposite_layer_blocker_retains_original_route(report):
 r=report['blocked'];assert r['unchanged'] and not r['report'].get('missing_api'),r

def test_expired_policy_does_not_start_a_native_mutation(report):
 r=report['expired'];assert r['unchanged'] and 'fault' not in r['report'] and not r['report'].get('missing_api'),r

def test_policy_partial_emission_restores_source_and_new_vias(report):
 r=report['partial-fault'];assert r['unchanged'] and 'RuntimeError' in r['report'].get('fault',''),r

def test_expiring_one_candidate_keeps_the_complete_incumbent_and_returns_a_result(report):
 r=report['partial-expiry'];assert r['unchanged'] and 'fault' not in r['report'] and r['report']['adopted_chains']==0,r
