import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('matrix-stage')
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_matrix_stage_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=60)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'report.json').read_text())

@pytest.mark.parametrize('face',['front','back'])
def test_sealed_matrix_includes_actual_controller_contacts_on_either_face(report,face):
 r=report[face];assert r['accepted'] is True and r['all_groups_connected'] and r['mcu_recorded'],r

def test_expired_matrix_never_saves_or_seals_partial_copper(report):
 r=report['expired'];assert not r['accepted'] and r['source_file_unchanged'] and r['no_receipt'],r


def test_a_missing_physical_controller_landing_cannot_be_omitted_from_proof(report):
 r=report['no-copper-terminal'];assert not r['accepted'] and r['source_file_unchanged'] and r['no_receipt'],r


def test_midphase_deadline_is_structured_rejection_with_saved_diagnostic(report):
 r=report['interrupted'];assert r['error'] is None and r['accepted'] is False and r['source_file_unchanged'] and r['no_receipt'] and r['diagnostic_exists'],r


def test_late_cleanup_cannot_claim_a_fresh_budget_or_publish_partial_copper(report):
 r=report['late-cleanup'];assert r['error'] is None and r['accepted'] is False and r['source_file_unchanged'] and r['no_receipt'] and r['diagnostic_exists'],r
