import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('full-collector-flow')
    r=subprocess.run([interpreter,str(Path(__file__).with_name('native_full_collector_flow_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=100)
    assert r.returncode==0,r.stdout+r.stderr
    return json.loads((out/'report.json').read_text())
@pytest.mark.parametrize('face',['front','back'])
def test_precompose_admission_includes_the_whole_native_matrix(report,face):
    r=report[face];assert r['accepted'] and r['proof'] and r['model_shared'] and r['source_preserved'],r
    assert r['matrix']['scope']=='matrix-with-controller' and r['matrix']['controller_connected']
    assert r['matrix']['collectors']['history']

def test_failed_full_matrix_cannot_accept_short_source_leads(report):
    r=report['rejected'];assert not r['accepted'] and r['unchanged'] and 'interface' not in r['report'],r


def test_full_matrix_failure_backtracks_source_geometry_before_rejecting_pose(report):
    r=report['retry'];assert r['accepted'] and r['proof'] and r['matrix_calls']>=2,r
    assert len(r['report']['trials'])>=2 and r['report']['trials'][0]['accepted'] is False


def test_local_collector_proof_timeout_leaves_the_complete_matrix_gate_in_control(report):
    r=report['local-proof-timeout'];assert r['accepted'] and r['proof'],r
    assert r['matrix']['collectors']['accepted'] is False and r['matrix']['collectors']['reason']


def test_rgb_access_is_reserved_before_matrix_and_survives_in_receipt(report):
    r=report['rgb-prerequisites'];assert r['accepted'] and r['proof'],r
    assert r['report']['key_data_access']['required']==8
    assert r['report']['matrix_contract']['prerequisite_tracks']


def test_one_frame_governs_preflight_joint_sources_and_full_matrix(report):
    from smilepcb.keyboard_routing.bank_geometry import port_outside_bounds
    r=report['outlet-frame'];assert r['accepted'] and r['proof'] and r['source_preserved'],r
    bounds=r['report']['access_preflight']['exit_bounds_nm'];assert bounds is not None
    for port in r['report']['assignment']['ports']:
        a=port['heading_origin_nm'];z=port['exit_nm']
        heading=tuple((z[i]>a[i])-(z[i]<a[i]) for i in (0,1))
        assert port_outside_bounds(z,heading,bounds,150000),port


def test_completed_matrix_uses_one_native_analysis_before_polishing(report):
    r=report['analysis-once'];assert r['accepted'] and r['proof'],r
    assert r['final_analysis_graph_reads']==[1],r['final_analysis_graph_reads']
