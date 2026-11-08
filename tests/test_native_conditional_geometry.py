"""Optional native checks for complete-bank creation proofs and exact cache restoration."""
import json
import os
from pathlib import Path
import subprocess
import pytest


@pytest.fixture(scope='module')
def native_report(tmp_path_factory):
    interpreter = os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:
        pytest.skip('Select native KiCad interpreter')
    root = Path(__file__).resolve().parents[1]
    output = tmp_path_factory.mktemp('conditional-geometry')
    completed = subprocess.run(
        [interpreter, str(Path(__file__).with_name('native_conditional_geometry.py')), str(output)],
        cwd=root, env={**os.environ, 'PYTHONPATH': str(root)},
        capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads((output / 'report.json').read_text())


@pytest.mark.parametrize('case', [
    'opposite_face_crossing', 'through_via_occupies_both_faces', 'second_via_hole_checked',
    'saved_via_self_exclusion', 'saved_via_actual_dimensions', 'other_same_net_via_hole_retained',
])
def test_pair_and_saved_via_native_guards(native_report, case):
    assert native_report['pairs_and_saved_vias'][case]


def test_frozen_index_differential_and_all_exception_paths(native_report):
    cases = native_report['frozen_index']['cases']
    assert native_report['frozen_index']['passed']
    assert native_report['frozen_index']['case_count'] == len(cases) == 18, cases
    assert all(case['passed'] for case in cases.values()), cases


def test_generated_via_replay_preserves_explicit_tenting_after_save(native_report):
    result=native_report['via_replay']
    assert result['snapshot_exact'],result
    assert result['original']==result['replayed']==result['reloaded']==[True,True],result
