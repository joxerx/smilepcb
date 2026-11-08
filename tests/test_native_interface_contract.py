import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('interface-contract')
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_interface_contract_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=45)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'report.json').read_text())
def test_same_face_interface_survives_native_json_and_schematic_path_roundtrip(report):
 assert report['valid']['accepted'] and report['valid']['native_contacts_exact']
@pytest.mark.parametrize('case',['terminal','copper','pin','deadline','alias','interior','heading','via_id','via_nm'])
def test_corruption_and_timeout_cannot_publish_an_interface(report,case):
 assert report[case]['rejected'] and report[case]['unchanged'],report[case]

def test_feeders_use_only_live_tip_and_cannot_reverse_into_controller_lead(report):
 assert all(report['policy'].values()),report['policy']


def test_via_runway_advertises_only_its_physical_tip_layer(report):
 assert all(report['via_terminals'].values()),report['via_terminals']

def test_ownership_uses_finite_trace_width_and_both_via_annuli(report):
 assert all(report['finite'].values()),report['finite']

@pytest.mark.parametrize('case',['missing','empty'])
def test_ordered_projects_cannot_fall_back_when_the_receipt_is_missing_or_empty(report,case):
 assert all(report[case].values()),report[case]


def test_fractional_component_centroids_are_quantized_at_native_contact_boundary(report):
 assert report['fractional_contacts']=={'track':True,'inside_zone':True,'outside_zone':True},report['fractional_contacts']


def test_native_clear_foreign_routes_cannot_seal_another_ports_launch(report):
 assert report['launch_reservation']==dict.fromkeys(('native_clear','foreign_blocked','own_allowed','other_side_allowed','via_blocked','outside_window_allowed'),True),report['launch_reservation']
