"""Saved native joint repair across changed geometry and protected copper."""
import json
import os
from pathlib import Path
import subprocess

import pytest
import sexpdata


@pytest.fixture(scope="module")
def joint_report(tmp_path_factory):
    interpreter = os.environ.get("SMILEPCB_KICAD_PYTHON")
    if not interpreter:
        pytest.skip("Select native KiCad interpreter")
    root = Path(__file__).resolve().parents[1]
    runtime = Path(os.environ.get("SMILEPCB_CONTROL_RUNTIME", str(root)))
    output = tmp_path_factory.mktemp("native-joint-repair") / "cases"
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(
        (str(runtime), str(Path(sexpdata.__file__).parent)))}
    result = subprocess.run(
        [interpreter, "-B", str(Path(__file__).with_name("native_joint_repair_probe.py")),
         "--source", str(runtime), "--output", str(output), "--case", "all", "--budget-s", "82"],
        cwd=root, env=environment, capture_output=True, text=True, timeout=360)
    # A completed behavioral failure still writes its evidence and is asserted
    # by the relevant test below. Native crashes remain fixture setup failures.
    assert result.returncode in (0, 1), result.stdout + result.stderr
    report = json.loads((output / "result.json").read_text())
    assert set(report["cases"]) == {"base", "changed", "protected"}, report
    return report


@pytest.mark.parametrize("case", ["base", "changed"])
def test_joint_repair_restores_every_saved_conductor_fragment(joint_report, case):
    result = joint_report["cases"][case]
    assert result["fixture_native_preflight"]
    assert result["accepted_as_expected"]
    assert all(result["audit"]["checks"].values()), result["audit"]["checks"]


def test_joint_repair_responds_to_geometry_with_new_identities(joint_report):
    assert all(joint_report["cross_case_checks"].values())


def test_joint_repair_preserves_protected_copper_and_rolls_back(joint_report):
    result = joint_report["cases"]["protected"]
    assert result["fixture_native_preflight"]
    assert result["rejected_as_expected"]
    assert result["rejected_files_exact"]


@pytest.mark.parametrize("case", ["base", "changed"])
def test_joint_repair_preserves_renamed_future_plane_access(joint_report, case):
    result = joint_report["cases"][case]
    assert result["accepted_as_expected"]
    audit = result["future_plane_audit"]
    assert all(audit["checks"].values()), audit


@pytest.mark.parametrize("case", ["base", "changed"])
def test_joint_repair_prunes_short_bus_chain_and_preserves_required_branch(joint_report, case):
    result = joint_report["cases"][case]
    assert result["accepted_as_expected"]
    audit = result["short_bus_audit"]
    assert all(audit["checks"].values()), audit
