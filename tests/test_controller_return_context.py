import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope='module')
def native_report():
    interpreter = os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:
        pytest.skip('Select native KiCad interpreter')
    root = Path(__file__).resolve().parents[1]
    probe = Path(__file__).with_name('native_controller_return_context_probe.py')
    result = subprocess.run(
        [interpreter, str(probe)], cwd=root, env={**os.environ, 'PYTHONPATH': str(root)},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_native_return_batch_finishes_with_a_bounded_geometry_setup_budget(native_report):
    result = native_report['success']
    assert result['accepted'], result
    assert result['island_sizes'] == [1, 1, 2]
    assert result['all_pads_reach_real_vias']
    assert result['reported_copper_exact'] and result['source_exact']


@pytest.mark.parametrize('mode', ['expiry', 'late_expiry', 'collision_expiry'])
def test_native_return_deadline_reports_timeout_and_rolls_back(native_report, mode):
    result = native_report[mode]
    assert result['exception'] == 'TimeoutError', result
    assert result['rollback_exact']


@pytest.mark.parametrize('mode', ['exception', 'late_exception'])
def test_native_return_unrelated_verification_exception_rolls_back(native_report, mode):
    result = native_report[mode]
    assert result['exception'] == 'ValueError', result
    assert result['reason'] == 'forced final verification failure'
    assert result['rollback_exact']


def test_standalone_plane_access_preserves_validation(native_report):
    assert native_report['validation'] == {'rejected': [True, True], 'source_exact': True}


def test_native_via_collision_with_time_remaining_retains_its_validation_error(native_report):
    result = native_report['collision']
    assert result['exception'] == 'ValueError' and 'solder land U3/1' in result['reason'], result
    assert result['rollback_exact']


def test_later_island_reuses_via_when_the_earlier_lead_merges_the_native_groups(native_report):
    result = native_report['merge']
    assert result['accepted'] and result['later_reuses_earlier_via'], result
    assert result['all_pads_reach_real_vias'] and result['source_exact']
