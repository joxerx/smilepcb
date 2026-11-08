import json,os,subprocess
from pathlib import Path
import pytest

def test_collector_joins_midspan_field_copper_on_its_actual_layer(tmp_path):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path/'result.json'
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_collector_tree_contact_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
 assert r.returncode==0,r.stdout+r.stderr
 d=json.loads(out.read_text());assert d['result']['accepted'] and d['all_pads_connected'] and d['source_exact'],d
 assert not d['new_vias'],d
 branches=[h['branch'] for h in d['result']['history'] if h.get('branch')]
 assert any(b['net']=='east' and b['target_nm']==[18000000,13000000] for b in branches),branches
 assert all(b['field_contact_class']=='PCB_TRACK' and b['collector_heading']==[0,-1] for b in branches),branches
