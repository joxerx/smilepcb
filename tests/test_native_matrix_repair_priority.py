import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope="module")
def report(tmp_path_factory):
    interpreter=os.environ.get("SMILEPCB_KICAD_PYTHON")
    if not interpreter:pytest.skip("Select native KiCad interpreter")
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp("matrix-repair-priority")/"result.json"
    result=subprocess.run([interpreter,str(Path(__file__).with_name("native_matrix_repair_priority_probe.py")),str(out)],
        cwd=root,env={**os.environ,"PYTHONPATH":str(root)},capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads(out.read_text())

@pytest.mark.parametrize("face",["front","back"])
def test_real_missing_matrix_reaches_repair_before_a_fine_retry_exhausts_it(report,face):
    row=report[face]
    assert row["complete"] and row["physical_proof"],row
    kinds=[event["kind"] for event in row["events"]]
    assert kinds==["feeders","feeders","repair"],row
    assert row["within_original_deadline"] and row["footprints_exact"],row

@pytest.mark.parametrize("face",["front-policy","back-policy"])
def test_each_matrix_router_keeps_the_live_escape_as_its_own_first_contact(report,face):
    row=report[face]
    assert row["internal_pad_shortcut_admitted"]==[False],row
    assert row["complete"],row
