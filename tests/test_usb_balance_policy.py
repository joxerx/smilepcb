"""Native regression of the generic USB balancing policy boundary."""
import json,os,subprocess
from pathlib import Path
import pytest


def test_native_usb_balancing_preserves_guards_and_caller_profile_scope(tmp_path):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run([interpreter,'-B',str(Path(__file__).with_name('native_usb_balance_policy_probe.py')),str(tmp_path)],
        cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+'\n'+run.stderr
    result=json.loads((tmp_path/'report.json').read_text())
    assert result['legacy_default']['rejected'] and result['higher_own_clearance']['rejected']
    assert result['interior_contact']['rejected'] and result['creation_callback']['repair_skew']
    assert result['caller_profile_scope']['dsn-process-v2']['callback_and_deadline']
