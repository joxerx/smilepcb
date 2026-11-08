import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 native=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not native:pytest.skip('Select Native KiCad')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('bridge-bundles');p=subprocess.run([native,str(Path(__file__).with_name('native_bridge_bundle_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=100);assert p.returncode==0,p.stdout+p.stderr;return json.loads((out/'result.json').read_text())

def test_joint_bundle_can_bridge_a_locked_crossing_on_the_other_face(report):
 r=report['bridges'];assert r['report'].get('changed_groups',0)>0 and r['vias']>=4 and not r['unchanged'],r
 assert r['contacts_exact'] and r['footprints_exact'] and r['locked_exact'],r

def test_joint_two_layer_refinement_stays_inside_its_physical_scope(report):
 r=report['outside'];assert r['unchanged'] and r['report'].get('changed_groups')==0,r

def test_partial_add_in_a_joint_bridge_restores_both_nets_and_vias(report):
 r=report['partial-fault'];assert r['unchanged'] and r['contacts_exact'] and 'RuntimeError' in r['report'].get('fault',''),r

def test_joint_geometry_gain_cannot_override_the_complete_area_quality_veto(report):
 r=report['quality-veto'];assert r['unchanged'] and r['contacts_exact'] and r['report'].get('changed_groups')==0 and 'fault' not in r['report'],r
