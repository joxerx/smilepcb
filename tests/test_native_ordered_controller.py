import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('ordered-controller')
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_ordered_controller_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=60)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'report.json').read_text())
def test_precompose_flow_carries_ordered_physical_banks_to_one_cad_qmk_model(report):
 r=report['valid'];assert r['report']['accepted'] and r['pins_shared'],r
 assert r['report']['interface']['schema']==1 and len(r['report']['interface']['ports'])==4

def test_expired_preparation_has_no_partial_geometry_or_receipt(report):
 r=report['expired'];assert not r['report']['accepted'] and r['unchanged'] and 'interface' not in r['report'],r

def test_native_same_face_miss_remains_available_to_full_physical_routing(report):
 r=report['blocked'];assert 'A0' in r['report']['access_preflight']['excluded_gpios'],r
 assert r['report']['accepted'] and r['new_tracks_exact'] and r['pins_shared'],r
 assert len(r['report']['interface']['ports'])==4,r

def test_a_failed_common_turn_retries_a_different_native_pitch_in_the_same_shared_budget(report):
 r=report['retry'];assert r['report']['accepted'] and r['new_tracks_exact'] and r['bundle_calls']>=2 and len(r['report']['trials'])>=2,r
 assert r['report']['interface']['tracks'] and all(q['accepted'] is False for q in r['report']['trials'][:-1])

def test_real_usb_pair_is_connected_before_bank_candidates_can_occupy_its_corridor(report):
 r=report['usb-first'];assert r['report']['accepted'] and r['new_tracks_exact'] and r['usb_first'] and r['report']['usb']['accepted'],r


def test_fixed_mcu_functions_are_reserved_with_matrix_before_composition(report):
 r=report['fixed-signals'];assert r['report']['accepted'],r
 assert 'enable' in r['reserved_signals'],r
 assert r['new_tracks_exact'] and r['pins_shared'],r


def test_fixed_function_handoff_cannot_omit_ports_or_fall_back_without_receipt(report):
 assert all(report['fixed-signals']['fixed_proof'].values()),report['fixed-signals']
 assert len(report['fixed-signals']['fixed_proof'])==4


def test_fixed_controller_copper_is_present_during_gpio_admission(report):
 assert report['fixed-signals']['fixed_before_probe'],report['fixed-signals']


def test_gpio_admission_sees_fixed_function_forward_reservations(report):
 assert report['fixed-signals']['fixed_promises_before_probe'],report['fixed-signals']
