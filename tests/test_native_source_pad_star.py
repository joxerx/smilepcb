"""Native source ownership regressions; no routing or board persistence."""
import json
import os
from pathlib import Path
import subprocess
import pytest


def test_native_source_pad_contact_star(tmp_path):
    native=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not native:pytest.skip('Select Native KiCad')
    here=Path(__file__).resolve().parent
    root=Path(os.environ.get('SMILEPCB_STAR_SUBJECT',str(here.parent)))
    staged=here/'source_pad_star.py'
    helper=staged if staged.exists() else root/'smilepcb/keyboard_routing/source_pad_star.py'
    output=tmp_path/'source-pad-star.json'
    run=subprocess.run([native,str(here/'native_source_pad_star_probe.py'),str(helper.parent),str(output)],
                       env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+run.stderr
    result=json.loads(output.read_text())
    assert len(result)==8 and all(result.values()),result
