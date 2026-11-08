import json,os,subprocess
from pathlib import Path
import pytest
@pytest.fixture(scope='module')
def report(tmp_path_factory):
 native=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not native:pytest.skip('Select Native KiCad')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('bundle-scope');p=subprocess.run([native,str(Path(__file__).with_name('native_bundle_scope_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=45);assert p.returncode==0,p.stdout+p.stderr;return json.loads((out/'result.json').read_text())

def test_mcu_grouping_does_not_rewrite_distant_simplifiable_routes(report):
 assert report['outside']['unchanged'] and report['outside']['report'].get('changed_groups')==0,report

def test_focus_is_not_a_global_disable_switch(report):
 assert report['inside']['report'].get('changed_groups',0)>0 and not report['inside']['unchanged'],report
