"""Native proposal/fallback contact controls; no whole-board claim."""
import json,os,subprocess
from pathlib import Path
import pytest


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('same-pad-face')
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_same_pad_face_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=40)
    assert run.returncode==0,run.stdout+run.stderr
    return json.loads((out/'report.json').read_text())


@pytest.mark.parametrize('face,layer',[('front',0),('back',2)])
def test_actual_same_face_pad_bridge_makes_preseeded_access_vias_redundant(report,face,layer):
    r=report[face+'-simple'];assert r['joined']==1 and r['native_connected'] and r['saved_native_connected'],r
    assert r['added_layers']==[layer] and not r['new_vias'],r
    assert r['native_connected_after_cleanup'] and r['remaining_access_vias']==0,r
    assert all(r[k] for k in ('original_copper_exact','footprints_exact','original_clearances_exact','source_file_exact','all_new_paths_physically_clear')),r


@pytest.mark.parametrize('face,layer',[('front',0),('back',2)])
def test_foreign_blocked_pad_face_retains_unrestricted_other_face_fallback(report,face,layer):
    r=report[face+'-blocked'];assert r['joined']==1 and r['native_connected'] and r['saved_native_connected'],r
    assert r['attempts'][0]['pad_face_proposal']['reason']=='blocked' and r['attempts'][0]['searches'],r
    assert r['added_layers']==[2 if layer==0 else 0] and not r['new_vias'] and r['remaining_access_vias']==2,r
    assert all(r[k] for k in ('original_copper_exact','footprints_exact','original_clearances_exact','source_file_exact','all_new_paths_physically_clear')),r


@pytest.mark.parametrize('face',['front','back'])
def test_actual_live_source_rejects_raw_pad_shortcut_and_keeps_verified_head(report,face):
    r=report[face+'-source'];assert r['joined']==1 and r['native_connected'] and r['saved_native_connected'],r
    assert r['attempts'][0]['pad_face_proposal']['reason']=='blocked' and r['raw_pad_shortcut_rejected'] and r['entry_live_admission_exact'],r
    assert all(r[k] for k in ('original_copper_exact','footprints_exact','original_clearances_exact','source_file_exact','all_new_paths_physically_clear')),r


@pytest.mark.parametrize('face',['front','back'])
@pytest.mark.parametrize('mode',['different','compound'])
def test_different_faces_and_numbered_pad_aliases_keep_existing_contact_fallback(report,face,mode):
    r=report[face+'-'+mode];assert r['joined']==1 and r['native_connected'] and r['saved_native_connected'],r
    assert r['attempts'][0].get('pad_face_proposal') is None and r['attempts'][0]['searches'],r
    assert r['native_connected_after_cleanup'] and not r['new_vias'],r
    assert all(r[k] for k in ('original_copper_exact','footprints_exact','original_clearances_exact','source_file_exact','all_new_paths_physically_clear')),r
