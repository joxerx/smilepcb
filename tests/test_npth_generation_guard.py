"""Native NPTH rule and temporary-exchange proposal guard regressions."""
import json,os,subprocess
from pathlib import Path
import pytest


def test_new_copper_fits_native_and_exchange_hole_clearances(tmp_path):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for native hole checks')
 root=Path(__file__).resolve().parents[1];out=tmp_path/'report.json'
 run=subprocess.run([interpreter,str(Path(__file__).with_name('native_npth_guard_probe.py')),str(out)],cwd=root,
                    env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
 assert run.returncode==0,run.stdout+'\n'+run.stderr
 assert json.loads(out.read_text())==dict.fromkeys(('native_gap_retained','dsn_guard_band_rejected','dsn_clear_path_retained','strict_native_hole_rule','strict_rule_across_buckets','same_net_plated_join'),True)


@pytest.mark.parametrize('diameter',[200000,1750000,4100000,8000000])
def test_generated_gap_encloses_actual_exchange_polygon_at_every_angle(diameter):
 import math,re
 from smilepcb.keyboard_exchange import adapt_npth_keepouts,polygonize_circles,npth_exchange_clearance_nm
 native=250000;router=150000
 text='(pcb "guard" (unit um) (structure (keepout "" (circle F.Cu '+str((diameter+2*native)/1000)+' 0 0))) (library) (wiring))'
 text,_=adapt_npth_keepouts(text,holes_um=[diameter/1000],native_hole_clearance_um=native/1000,router_clearance_um=router/1000)
 text,_=polygonize_circles(text)
 body=re.search(r'\(polygon F.Cu 0 ([^()]+)\)',text)[1];values=list(map(float,body.split()))
 maximum=max(math.hypot(x,y) for x,y in zip(values[::2],values[1::2]))*1000
 assert npth_exchange_clearance_nm(native,diameter)>maximum+router-diameter/2


@pytest.mark.parametrize('values',[(True,1000000),(250000,False),(0,1000000),(250000,0),(250000.,1000000),(None,1000000)])
def test_exchange_generation_guard_rejects_invalid_dimensions(values):
 from smilepcb.keyboard_exchange import npth_exchange_clearance_nm
 with pytest.raises(ValueError):npth_exchange_clearance_nm(*values)
