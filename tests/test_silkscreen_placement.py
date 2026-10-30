"""Packing rejects ink collisions before an expensive full-board route."""
import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def native_report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for native placement checks')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('silkscreen-packing')
 r=subprocess.run([interpreter,str(Path(__file__).with_name('native_silkscreen_placement_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=60)
 assert r.returncode==0,r.stderr+r.stdout
 return json.loads((out/'report.json').read_text())

def test_native_packing_rejects_overlapping_ink_outside_separate_courtyards(native_report):
 row=native_report['overlap']
 assert row['courtyards_overlap'] is False and row['native_ink_overlap'] is True
 assert row['accepted'] is False

@pytest.mark.parametrize('name',['separated','opposite'])
def test_legal_separation_and_opposite_side_ink_remain_packable(native_report,name):
 assert native_report[name]['accepted'] is True

def test_packing_checks_never_change_library_footprints(native_report):
 assert all(native_report[name]['source_unchanged'] for name in ('overlap','separated','opposite'))

def test_silkscreen_is_not_a_copper_obstacle(native_report):
 assert native_report['opposite_copper_accepted'] is True
