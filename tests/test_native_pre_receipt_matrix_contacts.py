import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope="module")
def native_report(tmp_path_factory):
    interpreter=os.environ.get("SMILEPCB_KICAD_PYTHON")
    if not interpreter:pytest.skip("Select native KiCad interpreter")
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp("matrix-hygiene-contacts")
    run=subprocess.run([interpreter,str(Path(__file__).with_name("native_pre_receipt_matrix_contacts_probe.py")),str(out)],cwd=root,
        env={**os.environ,"PYTHONPATH":str(root)},capture_output=True,text=True,timeout=60)
    assert run.returncode==0,run.stdout+run.stderr
    return json.loads((out/"report.json").read_text())

@pytest.mark.parametrize("face",["front","back"])
def test_native_cleanup_keeps_interior_plated_opposite_face_and_filled_contacts(native_report,face):
    row=native_report[face]
    assert row["pad_group_sizes"]==[2,3],row
    assert row["duplicates_and_tail_removed"] and row["all_actual_pad_contacts_exact"],row
    assert row["source_exact"] and row["interior_pth_via_and_plane_leads_retained"] and row["saved_copper_exact"],row

@pytest.mark.parametrize("face",["front","back"])
@pytest.mark.parametrize("failure,error",[("contact-loss","ValueError"),("expired","TimeoutError")])
def test_native_rejected_cleanup_restores_copper_and_refilled_contacts(native_report,face,failure,error):
    row=native_report[face+"-"+failure]
    assert row["error"]==error and row["copper_and_pads_exact"] and row["refilled_contacts_exact"],row
