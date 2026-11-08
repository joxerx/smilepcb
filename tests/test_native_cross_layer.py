import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select Native KiCad')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('cross-layer');r=subprocess.run([interpreter,str(Path(__file__).with_name('native_cross_layer_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=120);assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'result.json').read_text())
def test_whole_detour_is_replaced_across_disposable_vias(report):
 r=report['geometry'];assert r['chain_count']==1 and r['max_chain_objects']==7 and r['report'].get('adopted_chains',0)>0,r
 assert r['horizontal_nm']==0 and r['contacts_exact'] and r['footprints_exact'],r

def test_protected_copper_is_exact(report):
 r=report['protected'];assert r['unchanged'] and not r['report'].get('missing_api'),r

def test_expired_geometry_does_not_start_a_mutation(report):
 r=report['expired'];assert r['unchanged'] and not r['report'].get('missing_api') and 'fault' not in r['report'],r

def test_partial_emission_fault_restores_every_removed_via(report):
 r=report['partial-fault'];assert r['unchanged'] and r['contacts_exact'] and 'RuntimeError' in r['report'].get('fault',''),r

def test_partial_expiry_returns_the_exact_incumbent(report):
 r=report['partial-expiry'];assert r['unchanged'] and r['contacts_exact'] and 'fault' not in r['report'] and not r['report'].get('missing_api'),r

def test_whole_area_quality_veto_is_checked_before_geometry_is_selected(report):
 r=report['quality-veto'];assert r['unchanged'] and r['contacts_exact'] and r['report'].get('adopted_chains')==0 and 'fault' not in r['report'],r
