import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
    native=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not native:pytest.skip('Select Native KiCad')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('obstacle-bundle')
    run=subprocess.run([native,str(Path(__file__).with_name('native_obstacle_bundle_probe.py')),str(out)],env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=90)
    assert run.returncode==0,run.stdout+run.stderr
    return json.loads((out/'result.json').read_text())

def test_native_bundle_finds_a_real_detour_and_commits_only_after_virtual_proof(report):
    r=report['real-detour'];assert r['plan'].get('planned'),r
    assert r['readonly'] and r['result'].get('accepted') and not r['unchanged'],r
    assert r['contacts_exact'] and r['footprints_exact'],r
    assert r['plan']['search']['expanded']>0 and r['plan']['axis'],r

def test_atomic_corridor_commit_restores_after_partial_original_deletion(report):
    r=report['partial-delete'];assert r['plan'].get('planned'),r
    assert 'RuntimeError' in r['result'].get('fault','') and r['unchanged'] and r['contacts_exact'],r

def test_global_quality_veto_restores_exact_source_after_a_complete_corridor(report):
    r=report['quality-veto'];assert r['plan'].get('planned') and not r['result'].get('accepted'),r
    assert r['unchanged'] and r['contacts_exact'],r
