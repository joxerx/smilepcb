import json,os,subprocess
from pathlib import Path
import pytest

@pytest.fixture(scope="module")
def native_report(tmp_path_factory):
    interpreter=os.environ.get("SMILEPCB_KICAD_PYTHON")
    if not interpreter:pytest.skip("Select native KiCad interpreter")
    root=Path(__file__).resolve().parents[1];out=tmp_path_factory.mktemp("pre-receipt-hygiene")
    run=subprocess.run([interpreter,str(Path(__file__).with_name("native_pre_receipt_matrix_hygiene_probe.py")),str(out)],cwd=root,
        env={**os.environ,"PYTHONPATH":str(root)},capture_output=True,text=True,timeout=90)
    assert run.returncode==0,run.stdout+run.stderr
    return json.loads((out/"report.json").read_text())

@pytest.mark.parametrize("face",["front","back"])
def test_owned_matrix_duplicate_and_tail_retire_before_fixed_capture(native_report,face):
    row=native_report[face]
    assert row["complete"] and row["duplicate_retired"] and row["spur_retired"],row
    assert row["trunk_retained"] and row["source_exact"] and row["footprints_exact"],row
    assert row["saved_exact"] and row["all_actual_matrix_pads_proved"],row
    assert row["own_clearances_nm"]==[150000],row


@pytest.mark.parametrize("face",["front-expired","back-expired"])
def test_optional_expired_hygiene_retains_proved_matrix_and_reports_incumbent(native_report,face):
    row=native_report[face]
    assert row["complete"] and row["all_actual_matrix_pads_proved"],row
    assert not row["duplicate_retired"] and not row["spur_retired"],row
    assert row["source_exact"] and row["footprints_exact"] and row["saved_exact"],row
    assert row["hygiene"]["retained_incumbent"] and row["hygiene"]["removed_ids"]==[],row
