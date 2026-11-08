import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('bundle-geometry')
    env={**os.environ,'PYTHONPATH':os.pathsep.join(filter(None,(str(root),os.environ.get('PYTHONPATH'))))}
    r=subprocess.run([interpreter,'-X','faulthandler','-B',str(Path(__file__).with_name('native_controller_bundle_geometry_probe.py')),str(out)],cwd=root,env=env,capture_output=True,text=True,timeout=60)
    assert r.returncode==0,r.stdout+r.stderr
    return json.loads((out/'report.json').read_text())

@pytest.mark.parametrize('case',['wide_same_layer','via_track'])
def test_wide_free_space_bank_turns_toward_field_on_actual_track_terminals(report,case):
    r=report[case]
    assert r['accepted'] and len(r['groups'])==1,r
    assert len(r['groups'][0]['nets'])==9 and r['north'],r
    assert r['source_exact'] and r['footprints_exact'] and r['contacts_exact'],r
    assert r['via_metadata_exact'],r

def test_staggered_via_track_tips_align_without_losing_portal_pitch(report):
    r=report['staggered_via_rail']
    assert r['accepted'] and r['one_rail'],r
    assert len(r['paths'])==9,r
    assert all(b-a==500000 for a,b in zip(r['pitch_nm'],r['pitch_nm'][1:])),r
    assert r['source_exact'] and r['footprints_exact'] and r['contacts_exact'] and r['via_metadata_exact'],r


def test_rail_alignment_preserves_existing_nonuniform_lane_spacing(report):
    r=report['nonuniform_via_rail']
    assert r['accepted'] and r['one_rail'] and len(r['paths'])==9,r
    assert r['tangent_exact'],r
    assert r['source_exact'] and r['footprints_exact'] and r['contacts_exact'] and r['via_metadata_exact'],r


def test_aligned_via_carrier_reaches_capacity_as_one_published_physical_group(report):
    r=report['composed_via_carrier']
    assert r['alignment_one_rail'] and r['accepted'] and r['north'],r
    assert len(r['capacity_groups'])==1 and len(r['capacity_groups'][0])==9,r
    assert r['inventory_exact'] and r['source_exact'] and r['footprints_exact'],r
    assert r['contacts_exact'] and r['via_metadata_exact'],r
