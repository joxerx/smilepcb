"""Generated matrix field segments can be replaced during native fallback."""
import json
import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope='module')
def editable_field_report(tmp_path_factory):
    interpreter = os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:
        pytest.skip('Select native KiCad interpreter')
    import sexpdata

    root = Path(__file__).resolve().parents[1]
    configured = os.environ.get('SMILEPCB_EDITABLE_FIELD_OUTPUT')
    out = Path(configured) if configured else tmp_path_factory.mktemp('editable-field-fallback')
    out.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        'PYTHONPATH': os.pathsep.join((str(root), str(Path(sexpdata.__file__).parent),
                                       os.environ.get('PYTHONPATH', ''))),
    }
    process = subprocess.run(
        [interpreter, '-B', str(root / 'tests/native_editable_field_fallback_probe.py'), str(out)],
        cwd=root, env=env, capture_output=True, text=True, timeout=180)
    (out / 'probe.stdout.log').write_text(process.stdout)
    (out / 'probe.stderr.log').write_text(process.stderr)
    assert process.returncode == 0, process.stdout + process.stderr
    return json.loads((out / 'report.json').read_text())


def test_real_fallback_can_replace_generated_field_and_roll_back_commit_fault(editable_field_report):
    accepted = editable_field_report['accepted_case']
    assert accepted['accepted'] and accepted['source_mode'] == 'native_pads'
    assert accepted['saved_reload_exact'] and accepted['caller_reload_exact']
    assert accepted['native_proof']['accepted'] and accepted['reload_proof']['accepted']
    assert accepted['retired_field_ids'] == [accepted['split']['retired']]
    assert len(accepted['retired_orphan_ids']) == 1
    assert accepted['orphan_removed_before_caller_commit']
    assert accepted['immutable_copper_exact'] and accepted['model_unchanged']
    assert accepted['scope_unchanged']
    rollback = editable_field_report['rollback_case']['rollback']
    assert rollback == {
        'rejected': True,
        'route_called': True,
        'split_applied': True,
        'caller_exact': True,
        'model_exact': True,
        'raw_physical_exact': True,
        'scope_exact': True,
        'commit_proof_calls': 1,
        'retired_field_id_restored': True,
        'field_exact': True,
        'head_exact': True,
        'orphan_removed_before_caller_commit': True,
        'orphan_absent_after_rollback': True,
    }
