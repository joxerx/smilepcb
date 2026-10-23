"""Fresh two-layer routing with explicit controller/key access before signals.

Native attempts follow controller, rows, columns, and physical RGB snake order.
If they stall, a single bounded global pass may reroute generated copper only,
starting from the checked recipe seed. Completion and QMK gates remain separate.
"""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from smilepcb.keyboard_tools import load_tools, configure_kicad
from smilepcb.keyboard_firmware import file_hashes
from smilepcb.keyboard_phases import ordered_nets
from smilepcb.keyboard_routing.generated_seed import write_manifest

PROFILE = "dsn-safe-v1"


def main():
    tools = load_tools(sys.argv[1])
    base = ROOT / ".lab/public-controller"
    source = base / sys.argv[3] / sys.argv[4] / "project"
    out = base / sys.argv[2]
    source_hashes = file_hashes(source)
    out.mkdir(exist_ok=False)
    configure_kicad(tools, out)
    from smilepcb.keyboard_build import BRIDGE
    from smilepcb.lab_tools import worker, rule_checks
    from smilepcb.runtime import environment

    project = out / "project"
    shutil.copytree(source, project)
    board = project / "keyboard.kicad_pcb"
    cfg = json.loads((project / ".smilepcb-keyboard.json").read_text())
    started = time.perf_counter()
    history = []

    def call(operation, tag, script=BRIDGE, **kwargs):
        tick = time.perf_counter()
        request = dict(operation=operation, board=str(board),
                       generation_profile=PROFILE, **kwargs)
        result = worker(script, request, out / (tag + ".log"), timeout_s=90)
        (out / (tag + ".json")).write_text(json.dumps(result, indent=2) + "\n")
        history.append({"stage": tag, "elapsed_s": time.perf_counter() - tick,
                        "connections": result.get("connections"),
                        "gaps": result.get("pad_group_gaps")})
        print(tag, "connections", result.get("connections"),
              "gaps", result.get("pad_group_gaps"), flush=True)
        return result

    call("usb", "usb", script=Path(__file__).with_name("route_native.py"))
    for operation, tag, budget in (("key_data_access", "key-data-access", 70),
                                   ("controller_ports", "controller-ports", 70)):
        proof = call(operation, tag, budget_s=budget)
        if proof["accepted"] is not True:
            (out / "failure.json").write_text(json.dumps(
                {"complete": False, "stage": tag, "result": proof}, indent=2) + "\n")
            raise ValueError("Joint seed preparation failed: " + tag)
    call("local_modules", "local-modules", via_to_smd_clearance_nm=200000)
    returns = call("controller_return_tree", "controller-return-tree", budget_s=75)
    if returns["accepted"] is not True:
        raise ValueError("Controller return tree failed within its declared budget")

    # Keep the clean generated seed before long native attempts. It is never
    # reconstructed from a stalled routed board or an old accepted checkpoint.
    seed = out / "prepared-seed"
    seed.mkdir()
    shutil.copytree(project, seed / "project")
    for name in ("controller-ports.json", "key-data-access.json", "controller-return-tree.json"):
        shutil.copy2(out / name, seed / name)
    write_manifest(seed / "project", generation_profile=PROFILE)

    phases = ordered_nets(cfg)
    selected = "ordered-native"
    native_error = None
    try:
        for phase, nets in phases:
            if not nets:
                continue
            for index, grid in enumerate((500000, 125000)):
                call("components", phase + "-" + str(index), include_nets=nets,
                     grid_step_nm=grid, connection_budget_s=3,
                     completion_search=True,
                     track_width_nm=200000 if phase == "control" else 150000)
            call("verify", phase + "-connectivity")
        call("planes", "planes")
        call("stitch", "stitch")
        independent = call("verify", "independent-native")
        checks = rule_checks(project, out / "ordered-native-checks", "keyboard", timeout_s=120)
        if (checks["erc"]["severity"] or checks["drc"]["severity"].get("error")
                or checks["drc"]["schematic_parity"]):
            raise ValueError("Native ordered attempt has physical or parity violations")
        if independent["pad_group_gaps"]:
            raise ValueError("Native ordered attempt has remaining pad groups")
    except (ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
        native_error = str(error)
        selected = "checked-generated-global-recovery"
        archive = out / "native-attempt"
        archive.mkdir()
        project.rename(archive / "project")
        (archive / "result.json").write_text(json.dumps(
            {"complete": False, "reason": native_error}, indent=2) + "\n")
        tag = sys.argv[2] + "/generated-global"
        command = [sys.executable, str(Path(__file__).with_name("route_global.py")),
                   sys.argv[1], tag, sys.argv[2] + "/prepared-seed"]
        print("checked global recovery from immutable generated seed", flush=True)
        with (out / "generated-global.log").open("w") as log:
            subprocess.run(command, cwd=ROOT, env=environment(), stdout=log,
                           stderr=subprocess.STDOUT, timeout=1000, check=True)
        global_out = base / tag
        shutil.copytree(global_out / "project", project)
        independent = json.loads((global_out / "independent.json").read_text())
        checks = json.loads((global_out / "native-checks/summary.json").read_text())
    # The cold entrypoint checks this standard location before native completion.
    (out / "native-checks").mkdir()
    (out / "native-checks/summary.json").write_text(json.dumps(checks, indent=2) + "\n")
    if file_hashes(source) != source_hashes:
        raise ValueError("Routing edited its placed source project")
    result = {"complete": False, "routing_scope": "completion/firmware gates pending",
              "selected_path": selected, "native_attempt_error": native_error,
              "generation_profile": PROFILE, "elapsed_s": time.perf_counter() - started,
              "history": history, "phases": phases, "rgb_order": cfg["layout"]["rgb_order"],
              "independent": independent, "native_checks": checks,
              "source_unchanged": True, "hardware_tested": False}
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print("routing diagnostic", selected, "gaps", independent["pad_group_gaps"], flush=True)


if __name__ == "__main__":
    main()
