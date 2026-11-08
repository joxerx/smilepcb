"""A returned path is insufficient unless every emitting path obeys its role."""
import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def report(tmp_path_factory):
 interpreter=os.environ.get('SMILEPCB_KICAD_PYTHON')
 if not interpreter:pytest.skip('Set SMILEPCB_KICAD_PYTHON for native field checks')
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp('field-policy');r=subprocess.run([interpreter,str(Path(__file__).with_name('native_field_policy_probe.py')),str(out)],cwd=root,env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
 assert r.returncode==0,r.stdout+r.stderr
 return json.loads((out/'report.json').read_text())

@pytest.mark.parametrize('name',['constructor_accepts','simple_column_legal','shelf_plan_rejected','invalid_emission_rejected_without_mutation','ordinary_routing_unchanged','obstacle_connected','all_graph_segments_column_legal'])
def test_native_role_contract_applies_to_candidate_and_emission(report,name):
 assert report[name] is True,report
