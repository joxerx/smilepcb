import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
    native=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not native:pytest.skip('Select Native KiCad')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('corridor-closure')
    run=subprocess.run([native,str(Path(__file__).with_name('native_corridor_transaction_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=60)
    assert run.returncode==0,run.stdout+run.stderr
    return json.loads((out/'result.json').read_text())

def test_both_layer_blockers_are_moved_and_restored_as_a_complete_workset(report):
    r=report['closure'];assert r['result'].get('accepted'),r
    assert r['result']['expanded_chains']==2 and len(r['result']['restored'])==2,r
    assert r['contacts_exact'] and r['footprints_exact'] and not r['unchanged'],r

def test_second_original_delete_failure_restores_every_original_object(report):
    r=report['partial-delete'];assert 'RuntimeError' in r['result'].get('fault',''),r
    assert r['unchanged'] and r['contacts_exact'],r

def test_blocker_restoration_exception_restores_primary_and_blocker_copper(report):
    r=report['restore-fault'];assert 'RuntimeError' in r['result'].get('fault',''),r
    assert r['unchanged'] and r['contacts_exact'],r
