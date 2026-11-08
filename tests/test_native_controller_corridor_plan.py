"""The explicit Native coupon is executed serially by the root agent."""
import json
import os
from pathlib import Path
import subprocess
import pytest


@pytest.mark.parametrize('variant',['original','translated-renamed','accessible-aux'])
def test_real_early_plan_permutation_reservations_and_saved_native_closure(tmp_path,variant):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select native KiCad interpreter')
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([interpreter,str(Path(__file__).with_name('native_controller_corridor_plan_probe.py')),str(tmp_path),variant],
        cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads((tmp_path/'report.json').read_text())
    assert all(report[key] for key in ('old_plan_rejects_real_assignment','via_annuli_both_faces',
        'foreign_field_blocked','owner_allowed','peer_forbidden','future_via_back_face_blocked',
        'own_bridge_allowed','one_time','only_declared_pad_bindings_changed','saved_actual_pad_closure',
        'issuance_obligations_preserved'))
    assert report['commit']['accepted'] and report['commit']['actual_pad_closure']

    if variant=='accessible-aux':assert report['auxiliary_issuance_preserved']
