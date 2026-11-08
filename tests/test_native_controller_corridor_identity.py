import json
import os
from pathlib import Path
import subprocess
import pytest


def test_same_net_roots_keep_distinct_headings_and_close_all_actual_pads(tmp_path):
    interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not interpreter:pytest.skip('Select Native KiCad interpreter')
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([interpreter,str(Path(__file__).with_name('native_controller_corridor_identity_probe.py')),str(tmp_path)],
        cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads((tmp_path/'report.json').read_text())
    assert report['accepted'] and report['same_net_roots']==2 and len({tuple(key) for key in report['source_keys']})==2
    assert all(report[key] for key in ('independent_headings','local_and_external_targets','saved_all_four_pad_closure','usb_and_source_exact'))
