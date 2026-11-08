"""Actual native prototype checks; no generator/compiler or safety promotion."""
import json,os,subprocess
from pathlib import Path
import pytest

def test_named_process_policy_binds_actual_constructor_and_worker_without_weakening_native_gates(tmp_path):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path/'native-process'
 run=subprocess.run([interpreter,str(Path(__file__).with_name('native_process_generation_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
 assert run.returncode==0,run.stdout+run.stderr
 r=json.loads((out/'report.json').read_text())
 assert r['native']['pocket_allowed'] and not r['dsn-safe-v1']['pocket_allowed'] and r['dsn-process-v2']['pocket_allowed']
 assert all(r[n]['source_exact'] for n in ('native','dsn-safe-v1','dsn-process-v2'))
 assert r['dsn-process-v2']['resolved']['via_to_smd_clearance_nm']==173000
 assert r['dsn-process-v2']['resolved']['via_to_hole_clearance_nm']==251000
 assert r['larger_native_floor']['resolved']['via_to_smd_clearance_nm']==300000
 assert r['larger_native_floor']['resolved']['via_to_hole_clearance_nm']==401000 and r['larger_native_floor']['near_foreign_pad_rejected']
 assert r['same_net_drill_land_rejected'] and r['independent_saved_land_gate_rejected']
 assert r['worker']['generation_profile']=='dsn-process-v2' and r['worker_context_reset']
 assert r['worker']['field_generation_profile']=='dsn-process-v2' and r['worker']['accepted'] and not r['worker']['controller_connected']
