"""Real native obstacle contracts; optional on machines without KiCad."""
import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope="module")
def native_report(tmp_path_factory):
 interpreter=os.environ.get("SMILEPCB_KICAD_PYTHON")
 if not interpreter:pytest.skip("Set SMILEPCB_KICAD_PYTHON for native physical checks")
 root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp("native-physical")
 completed=subprocess.run([interpreter,str(Path(__file__).with_name("native_physical_probe.py")),str(out)],cwd=root,env={**os.environ,"PYTHONPATH":str(root)},capture_output=True,text=True,timeout=60)
 assert completed.returncode==0,completed.stderr+"\n"+completed.stdout
 report=json.loads((out/"report.json").read_text())
 cli=Path(interpreter).resolve().parents[5]/"MacOS/kicad-cli"
 checked=subprocess.run([str(cli),"pcb","drc","--format","json","--output",str(out/"drc.json"),str(out/"rules/fixture.kicad_pcb")],capture_output=True,text=True,timeout=60)
 assert checked.returncode==0,checked.stderr
 report["native_drc"]=json.loads((out/"drc.json").read_text())
 return report

@pytest.mark.parametrize("kind",["board_shape","footprint_shape","board_text","footprint_text","footprint_field"])
def test_copper_artwork_blocks_a_crossing_route_in_both_oracles(native_report,kind):
 row=dict(native_report[kind])
 assert row.pop("source_unchanged") is True
 if "native_shape_crosses_probe" in row:assert row["native_shape_crosses_probe"] is True
 assert row["simple"] is False,row
 assert row["local"] is False,row

@pytest.mark.parametrize("kind",["silkscreen","opposite_face","registration_target"])
def test_non_copper_and_registration_targets_do_not_block_a_route(native_report,kind):
 row=dict(native_report[kind]);assert row.pop("source_unchanged") is True
 assert row=={"simple":True,"local":True}

def test_saved_project_netclass_clearance_applies_to_the_routed_net(native_report):
 row=native_report["netclass"]
 assert row["own"]["strict"]==508000,row
 assert row["too_close"]=={"simple":False,"local":False},row
 assert row["clear"]=={"simple":True,"local":True},row


def test_same_net_contacts_remain_legal(native_report):
 assert native_report["netclass"]["same_net"]=={"simple":True,"local":True}

def test_strict_net_searches_beyond_an_obstacles_bucket(native_report):
 assert native_report["netclass"]["bucket_boundary"]=={"simple":False,"local":False}


def test_native_drc_confirms_the_strict_clearance_counterexample(native_report):
 findings=native_report["native_drc"]["violations"]
 assert any(row["type"]=="clearance" and "0.5080" in row["description"] and "0.2738" in row["description"] for row in findings),findings
