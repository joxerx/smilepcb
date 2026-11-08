import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Select native KiCad interpreter')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('deferred-fill')
 done=subprocess.run([interpreter,str(Path(__file__).with_name('native_deferred_fill_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
 assert done.returncode==0,done.stdout+done.stderr
 return json.loads((out/'result.json').read_text())
def test_deferred_movable_fill_opens_a_route_without_weakening_default_clearance(report):
 assert report['default_blocked'] and report.get('deferred_clear'),report
def test_deferred_fill_keeps_real_copper_and_rule_areas_as_obstacles(report):
 assert report.get('real_track_still_blocks') and report.get('keepout_still_blocks'),report
def test_deferred_fill_rejects_locked_unknown_or_rule_area_ids(report):
 assert report.get('unknown_rejected') and report.get('locked_rejected') and report.get('keepout_rejected'),report
def test_deferred_clearance_query_does_not_mutate_native_board(report):
 assert report.get('source_copper_and_components_exact'),report
