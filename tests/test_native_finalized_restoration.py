"""Finalized restoration credits only actual current filled-plane contacts."""
import json
import os
from pathlib import Path
import subprocess

import pytest
import sexpdata


@pytest.fixture(scope='module')
def report():
    interpreter = os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:
        pytest.skip('Select native KiCad interpreter')
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, 'PYTHONPATH': os.pathsep.join(
        (str(root), str(Path(sexpdata.__file__).parent), os.environ.get('PYTHONPATH', '')))}
    result = subprocess.run([interpreter, '-B', str(Path(__file__).with_name('native_finalized_restoration_probe.py'))],
                            env=environment, capture_output=True, text=True, timeout=70)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_real_plane_preserves_deleted_bridge_without_redundant_route(report):
    assert report['plane']['accepted'], report['plane']
    assert report['plane']['added'] == 0
    assert report['plane']['zone_count'] == 1


@pytest.mark.parametrize('case', ['nozone', 'split', 'missing', 'detached', 'merged', 'changed', 'inventory'])
def test_unproved_restoration_is_rejected(report, case):
    assert not report[case]['accepted'], report[case]


def test_failed_search_reattaches_original_zones(report):
    assert report['split']['zone_count'] == 2


def test_unsatisfied_obligation_is_actually_routed_then_refilled(report):
    assert report['repaired']['accepted'], report['repaired']
    assert report['repaired']['added'] > 0
    assert report['repaired']['zone_count'] == 2
    assert report['repaired']['zone_definitions_exact']


def test_missing_zones_and_unintended_merges_are_explicitly_rejected(report):
    assert 'attached ordinary zones' in report['nozone']['error']
    assert 'components merged' in report['merged']['error']
    assert 'detached' in report['detached']['error']
