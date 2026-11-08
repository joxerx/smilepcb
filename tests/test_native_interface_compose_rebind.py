"""The actual native finalizer preserves historical interface evidence."""
import json
import os
from pathlib import Path
import subprocess
import pytest


@pytest.fixture(scope='module')
def report(tmp_path_factory):
    interpreter = os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:
        pytest.skip('Select native KiCad interpreter')
    import sexpdata
    root = Path(__file__).resolve().parents[1]
    out = tmp_path_factory.mktemp('native-interface-compose')
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(root), str(Path(sexpdata.__file__).parent),
                                                     os.environ.get('PYTHONPATH', '')))}
    process = subprocess.run([interpreter, '-B', str(Path(__file__).with_name('native_interface_compose_rebind_probe.py')),
                              str(out)], cwd=root, env=env, capture_output=True, text=True, timeout=180)
    assert process.returncode == 0, process.stdout+process.stderr
    return json.loads((out/'report.json').read_text())


@pytest.mark.parametrize('face', ['front', 'back'])
def test_real_finalizer_keeps_retired_heads_and_live_control_through_compose(report, face):
    row = report[face]['valid']
    checks = ('accepted', 'native_full_matrix', 'original_receipt_relabelled', 'original_matrix_heads_absent',
              'historical_ids_preserved', 'live_control_exact', 'only_control_unresolved', 'paths_rebound',
              'current_copper_relabelled', 'generated_bindings_relabelled')
    assert all(row.get(key) is True for key in checks), row


@pytest.mark.parametrize('face', ['front', 'back'])
@pytest.mark.parametrize('case', ['reopened_matrix', 'missing_live_head', 'missing_receipt'])
def test_real_finalizer_rejects_incomplete_or_missing_interface_evidence(report, face, case):
    row = report[face][case]
    assert row['rejected'] and row['saved_revision_exact'], row
    reason = {'reopened_matrix': 'physical terminal connectivity', 'missing_live_head': 'interface copper changed',
              'missing_receipt': 'required ordered controller interface receipt'}[case]
    assert reason in row['error'], row


@pytest.mark.parametrize('face', ['front', 'back'])
@pytest.mark.parametrize('case', ['publish_failure', 'rebind_failure'])
def test_failed_real_publication_restores_pcb_and_policy_bytes(report, face, case):
    row = report[face][case]
    assert row['rejected'] and row['saved_revision_exact'], row
    assert 'injected compose publication failure' in row['error'], row
    written = row['written_before_failure']
    assert all(written[name] for name in ('keyboard.kicad_pcb', '.smilepcb-field.json', '.smilepcb-interfaces.json')), row
    if case == 'rebind_failure':
        assert written['.smilepcb-generated.json'], row
