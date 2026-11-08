import json,os,subprocess
from pathlib import Path
import pytest

def test_inventory_uses_physical_mcu_pad_contacts_and_bank_order(tmp_path):
    native=os.environ.get('SMILEPCB_KICAD_PYTHON')
    if not native:pytest.skip('Select Native KiCad')
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run([native,str(Path(__file__).with_name('native_obstacle_inventory_probe.py')),str(tmp_path)],env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+run.stderr
    q=json.loads((tmp_path/'result.json').read_text())
    assert q['unchanged'] and len(q['rows'])==2 and q['group_nets']==[['first','second']],q
    assert all(row['heading']==[1,0] and row['mcu_pad'] in ['1','2'] and row['window']['fixed_legs'] for row in q['rows']),q
    assert all(row['window']['source']==row['pad_position'] and not row['window']['prefix_legs'] for row in q['rows']),q
