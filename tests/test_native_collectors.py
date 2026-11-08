import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('collectors')/'report.json'
    r=subprocess.run([interpreter,str(Path(__file__).with_name('native_collectors_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=80)
    assert r.returncode==0,r.stdout+r.stderr
    return json.loads(out.read_text())

def test_compression_preserves_a_usable_first_contact_departure(report):
    r=report['compression'];assert r['accepted'] and r['usable'] and r['source_exact'] and r['pitch_nm']==350000,r

@pytest.mark.parametrize('kind',['tht','smd'])
def test_collector_connects_real_external_pads_and_preserves_sources(report,kind):
    r=report[kind];assert r['result']['accepted'] and r['all_connected'] and r['source_exact'],r
    assert len(r['result']['closed_nets'])==3

def test_roles_can_share_one_physical_collector_rail(report):
    assert report['tht']['mixed_group_count']==1,report['tht']

def test_expired_collectors_do_not_publish_partial_routing(report):
    r=report['expired'];assert not r['result']['accepted'] and r['unchanged'] and not r['result']['closed_nets'],r


def test_collector_can_turn_before_peeling_to_fields_behind_the_source_heading(report):
    r=report['turned'];assert r['result']['accepted'] and r['all_connected'] and r['source_exact'],r
    assert any(q.get('branch',{}).get('collector_heading') in ([1,0],[-1,0]) for q in r['result']['history'] if q.get('branch'))


def test_accepted_peels_keep_their_original_cohort_after_subsequent_peels(report):
    history=report['tht']['result']['history']
    accepted=[q for q in history if q.get('kind')=='peel' and q.get('accepted')]
    assert accepted
    assert all(q['branch']['net'] in q['nets'] for q in accepted),accepted


def test_opposite_collector_directions_do_not_drag_each_other_past_their_fields(report):
    r=report['opposite-collectors']['result'];assert r['accepted'] and len(r['closed_nets'])==3,r
    assert sorted(map(len,r['initial_groups']))==[1,2],r['initial_groups']


def test_collector_axis_can_cross_a_one_layer_barrier(report):
    r=report['layer-wall']
    assert r['result']['accepted'] and r['all_connected'] and r['source_exact'],r
    assert r['axis_portals']>0,r
