import json,os,subprocess
from pathlib import Path
import pytest


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('launch-lifecycle')/'report.json'
    result=subprocess.run([interpreter,str(Path(__file__).with_name('native_launch_lifecycle_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads(out.read_text())


def test_native_complete_interface_releases_only_its_virtual_departure(report):
    assert all(report['completed'].values()),report['completed']


def test_advanced_interface_replaces_captured_terminal_and_keeps_fixed_sources(report):
    assert all(report['advanced'].values()),report['advanced']


def test_reopened_native_connectivity_restores_the_departure_guard(report):
    assert all(report['reopened'].values()),report['reopened']


def test_one_disconnected_physical_alias_keeps_the_interface_live(report):
    assert all(report['alias'].values()),report['alias']


def test_successful_native_connection_immediately_retires_first_contact_guards(report):
    assert all(report['new-connection'].values()),report['new-connection']


def test_complete_native_matrix_refinement_retires_all_provisional_matrix_guards(report):
    assert all(report['refinement'].values()),report['refinement']


def test_source_aliases_alone_are_not_a_completed_field_connection(report):
    assert all(report['source-only'].values()),report['source-only']


def test_discarding_a_speculative_advance_retains_the_captured_native_source(report):
    assert all(report['speculative-rollback'].values()),report['speculative-rollback']
