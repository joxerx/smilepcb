"""Real native USB placement feedback, rollback and prepared-route reuse."""
import json
import os
from pathlib import Path
import subprocess
import pytest


def test_native_usb_placement_feedback(tmp_path):
    interpreter = os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:
        pytest.skip('Select native KiCad interpreter')
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(filter(None, (str(root), os.environ.get('PYTHONPATH'))))}
    run = subprocess.run([interpreter, '-B', str(Path(__file__).with_name('native_usb_placement_feedback_probe.py')), str(tmp_path)],
                         cwd=root, env=env, capture_output=True, text=True, timeout=90)
    assert run.returncode == 0, run.stdout + '\n' + run.stderr
    report = json.loads((tmp_path / 'report.json').read_text())
    assert report['retry']['accepted']
    assert report['retry']['trials'][0]['accepted'] is False
    assert report['rollback'] and report['expired'] and report['exception']
