"""Explicit Native integration gate for request-scoped corridor reservations."""
import json
import os
from pathlib import Path
import subprocess

import pytest


def test_native_corridor_reservations_preserve_fixed_copper(tmp_path):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for native corridor checks')
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run([interpreter,str(Path(__file__).with_name('native_corridor_reservations_probe.py')),str(tmp_path)],
        cwd=root,env={**os.environ,'PYTHONPATH':str(root),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=60)
    assert run.returncode==0,run.stderr+'\n'+run.stdout
    report=json.loads((tmp_path/'report.json').read_text())
    assert report==dict(foreign_blocked=True,owner_allowed=True,back_face_allowed=True,
        owner_still_obeys_physical_clearance=True,actual_no_prefix_leak=True,ghost_no_prefix_leak=True,
        via_both_faces_blocked=True,owner_emitted=True,usb_exact_after_reload=True,pads_exact_after_reload=True,two_layers=True,
        future_annulus_blocks_foreign_back_trace=True,future_annulus_owner_bridge_allowed=True,
        future_annulus_uses_exact_circle=True,future_annulus_owner_bridge_emitted=True)
