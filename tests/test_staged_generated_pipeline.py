"""Exercise rollback/seed provenance at the staged-driver boundary, without CAD."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def setup_driver(tmp_path, monkeypatch, *, native_gaps=0, reject_seed=False):
    spec = importlib.util.spec_from_file_location("native_staged_driver", ROOT / "recipes/stm32/route.py")
    driver = importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)
    driver.ROOT = tmp_path
    base = tmp_path / ".lab/public-controller"; source = base / "input/fixture/project"
    source.mkdir(parents=True)
    cfg = {"layout": {"rgb_order": [2, 1]}, "plane_nets": []}
    for name, value in {"keyboard.kicad_pcb": "placed only", "keyboard.kicad_pro": "{}",
                        "layout.kle.json": "[]", ".smilepcb-keyboard.json": json.dumps(cfg)}.items():
        (source / name).write_text(value)
    monkeypatch.setattr(driver, "load_tools", lambda _: {})
    monkeypatch.setattr(driver, "configure_kicad", lambda *_: None)
    monkeypatch.setattr(driver, "ordered_nets", lambda _: [("control", ["ctl"]), ("rows", ["r"]), ("columns", ["c"]), ("rgb", ["b", "a"])])
    monkeypatch.setattr(sys, "argv", ["script", "tools.json", "output", "input", "fixture"])
    calls = []
    checks = {"erc": {"severity": {}}, "drc": {"severity": {}, "schematic_parity": 0}}

    def worker(script, request, log, **kwargs):
        op = request["operation"]; calls.append((op, request))
        board = Path(request["board"])
        if op not in ("verify",):
            board.write_text(board.read_text() + "|" + op)
        if op == "verify":
            return {"pad_group_gaps": native_gaps}
        return {"accepted": not reject_seed, "connections": 1}

    def run(command, **kwargs):
        calls.append(("global", command))
        seed = base / "output/prepared-seed/project/keyboard.kicad_pcb"
        assert "|components" not in seed.read_text()
        target = base / "output/generated-global"
        import shutil
        shutil.copytree(seed.parent, target / "project")
        (target / "project/keyboard.kicad_pcb").write_text(seed.read_text() + "|global")
        (target / "native-checks").mkdir()
        (target / "native-checks/summary.json").write_text(json.dumps(checks))
        (target / "independent.json").write_text(json.dumps({"pad_group_gaps": 1}))

    monkeypatch.setitem(sys.modules, "smilepcb.keyboard_build", SimpleNamespace(BRIDGE=Path("native")))
    monkeypatch.setitem(sys.modules, "smilepcb.lab_tools", SimpleNamespace(worker=worker, rule_checks=lambda *_a, **_k: checks))
    monkeypatch.setitem(sys.modules, "smilepcb.runtime", SimpleNamespace(environment=lambda: {}))
    monkeypatch.setattr(driver.subprocess, "run", run)
    return driver, base, source, calls


def test_successful_native_path_retains_receipted_seed_and_never_claims_complete(tmp_path, monkeypatch):
    driver, base, source, calls = setup_driver(tmp_path, monkeypatch)
    driver.main()
    result = json.loads((base / "output/result.json").read_text())
    assert result["complete"] is False
    assert result["selected_path"] == "ordered-native"
    assert source.joinpath("keyboard.kicad_pcb").read_text() == "placed only"
    assert not any(name == "global" for name, _ in calls)
    assert [req["include_nets"] for op, req in calls if op == "components"] == [["ctl"], ["ctl"], ["r"], ["r"], ["c"], ["c"], ["b", "a"], ["b", "a"]]
    from smilepcb.keyboard_routing.generated_seed import validate_manifest
    assert validate_manifest(base / "output/prepared-seed/project")["completed_keyboard"] is False


def test_stalled_native_path_uses_only_immutable_seed_and_archives_attempt(tmp_path, monkeypatch):
    driver, base, source, calls = setup_driver(tmp_path, monkeypatch, native_gaps=7)
    driver.main()
    out = base / "output"
    assert "|components" in (out / "native-attempt/project/keyboard.kicad_pcb").read_text()
    assert (out / "project/keyboard.kicad_pcb").read_text().endswith("|global")
    assert json.loads((out / "result.json").read_text())["complete"] is False
    assert len([op for op, _ in calls if op == "global"]) == 1
    assert source.joinpath("keyboard.kicad_pcb").read_text() == "placed only"


def test_failed_joint_seed_does_not_start_signals_or_global_router(tmp_path, monkeypatch):
    driver, base, source, calls = setup_driver(tmp_path, monkeypatch, reject_seed=True)
    with pytest.raises(ValueError, match="Joint seed preparation"):
        driver.main()
    assert not any(op in ("components", "global") for op, _ in calls)
    assert not (base / "output/prepared-seed").exists()
