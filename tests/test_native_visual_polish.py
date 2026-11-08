import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('visual-polish')
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_visual_polish_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=60)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'result.json').read_text())
def test_native_column_becomes_vertical_diagonal_and_preserves_pads(report):
 for name in ('simple','obstacle'):
  q=report[name];assert q['horizontal_nm']==0 and q['report']['adopted_chains']==1 and q['footprints_exact'],q

def test_expired_column_refinement_retains_exact_incumbent(report):
 q=report['expired'];assert q['unchanged'] and 'fault' not in q['report'],q

def test_partial_emission_before_bookkeeping_restores_original_geometry(report):
 q=report['partial-fault'];assert q['unchanged'] and 'RuntimeError' in q['report'].get('fault',''),q

def test_native_duplicate_pruning_keeps_locked_representative(report):
 q=report['duplicates'];assert len(q['report']['removed_ids'])==1 and q['objects']==2 and q['footprints_exact'],q

def test_native_bridge_crosses_a_locked_foreign_route_without_horizontal_column(report):
 q=report['bridge'];assert q['report']['adopted_chains']==1 and q['vias']==2 and q['horizontal_nm']==18_000_000 and q['footprints_exact'],q

def test_native_column_avoids_through_holes_without_horizontal_tracks(report):
 q=report['hole-bypass'];assert q['report']['adopted_chains']==1 and q['horizontal_nm']==0 and q['footprints_exact'],q

def test_native_feeder_follows_foreign_lane_at_uniform_pitch(report):
 q=report['grouping'];assert q['report']['adopted_chains']>0 and q['footprints_exact'] and any(9_200_000<y<9_600_000 for y in q['bus_horizontal_y']),q
