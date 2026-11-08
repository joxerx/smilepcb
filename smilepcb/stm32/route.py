"""Fresh two-layer routing with explicit controller/key access before signals.

Native attempts follow controller, rows, columns, and physical RGB snake order.
If they stall, a single bounded global pass may reroute generated copper only,
starting from the checked recipe seed. Completion and QMK gates remain separate.
"""
from pathlib import Path
import json
import math
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from smilepcb.stm32.context import workspace,STAGED_GENERATION_PROFILE
from smilepcb.keyboard_tools import load_tools, configure_kicad
from smilepcb.keyboard_firmware import file_hashes
from smilepcb.keyboard_phases import ordered_nets
from smilepcb.keyboard_routing.generated_seed import write_manifest
from smilepcb.usb_policy import configured_usb_max_length_nm
from smilepcb.routing_quality import quality_from_args
from smilepcb.keyboard_routing.generated_provenance import tracked_worker, generated_ids, inventory, has_zones

PROFILE = STAGED_GENERATION_PROFILE



def recover_preplane_signals(project, output, independent, call, *, plane_nets, deadline, protected_ids=()):
    """Try ordinary signal negotiation, then joint field repair before plane fill."""
    project=Path(project);output=Path(output);board=project/'keyboard.kicad_pcb'
    current=independent;planes=set(plane_nets)
    def pending():
        return {net for net,count in current['pad_groups_missing'].items() if count>0 and net not in planes}
    if not pending() or has_zones(board):return current
    from smilepcb.keyboard_build import routing_transaction
    output.mkdir(parents=True,exist_ok=True)
    index=0
    def attempt(operation,**options):
        nonlocal index,current
        if deadline-time.perf_counter()<(95 if operation=='joint_repair' else 90):return False
        tag='preplane-'+str(index)+'-'+operation;index+=1
        def repair(candidate):
            budget=82;worker_options={}
            if operation=='joint_repair':
                remaining=deadline-time.perf_counter()
                if remaining<95:return current
                budget=min(150,remaining-13)
                worker_options['worker_timeout_s']=budget+8
            result=call(operation,tag=tag,board_override=candidate/board.name,
                protected_ids=list(protected_ids),budget_s=budget,**worker_options,**options)
            return {**current,'pad_group_gaps':result['pad_group_gaps'],
                'pad_groups_missing':result['pad_groups_missing']} if result.get('accepted') is True else current
        transaction=routing_transaction(project,output/tag,repair,current,deadline=deadline)
        (output/(tag+'.json')).write_text(json.dumps(transaction,indent=2)+'\n')
        if transaction['accepted']:current=call('verify',tag=tag+'-saved')
        return transaction['accepted']
    while pending():
        if not attempt('complete',strategy='signal_negotiate'):break
    if pending() and (project/'.smilepcb-field.json').exists():
        attempt('joint_repair',generated_ids=generated_ids(board))
    return current


def main():
    quality=quality_from_args(sys.argv[5:])
    tools = load_tools(sys.argv[1])
    base = workspace()
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
    process = json.loads((project / "keyboard.kicad_pro").read_text())
    control_width_mm = process["board"]["design_settings"]["rules"]["min_track_width"]
    if type(control_width_mm) not in (int, float) or not math.isfinite(control_width_mm) or control_width_mm <= 0:
        raise ValueError("Expected positive finite project track minimum")
    control_width_nm = round(control_width_mm * 1_000_000)
    started = time.perf_counter()
    history = []

    def call(operation, tag, script=BRIDGE, worker_timeout_s=90, **kwargs):
        tick = time.perf_counter()
        selected_board=kwargs.pop("board_override",board)
        if operation=='joint_repair':worker_timeout_s=min(worker_timeout_s,kwargs.get('budget_s',82)+8)
        request = dict(operation=operation, board=str(selected_board),
                       generation_profile=PROFILE,routing_quality=quality, usb_max_length_nm=configured_usb_max_length_nm(cfg), **kwargs)
        result = tracked_worker(worker, script, request, out / (tag + ".log"), timeout_s=worker_timeout_s)
        (out / (tag + ".json")).write_text(json.dumps(result, indent=2) + "\n")
        if result.get('generation_profile')!=PROFILE:
            raise ValueError('Native worker generation profile differs from requested policy')
        history.append({"stage": tag, "elapsed_s": time.perf_counter() - tick,
                        "connections": result.get("connections"),
                        "gaps": result.get("pad_group_gaps")})
        print(tag, "connections", result.get("connections"),
              "gaps", result.get("pad_group_gaps"), flush=True)
        return result

    existing_field=(project/'.smilepcb-field.json').exists()
    if existing_field:
        # Placement already closed these obligations atomically. Repeating their
        # producers would mutate the very interface/field being handed over.
        field=call('field_verify','matrix-field')
        if PROFILE=='dsn-process-v2' and field.get('field_generation_profile')!=PROFILE:
            raise ValueError('Existing field generation profile differs from requested policy')
        if field.get('scope')!='matrix-with-controller':
            raise ValueError('Existing field receipt is not a complete controller-connected matrix')
    else:
        if cfg.get('routing_preparation',{}).get('matrix')=='collectors-v1':
            raise ValueError('Missing required pre-compose collector matrix receipt')
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

        field=call('matrix_field','matrix-field',budget_s=220,include_controller=True,worker_timeout_s=240)
    if field['accepted'] is not True or field.get('controller_connected') is not True:
        raise ValueError('Incomplete matrix field; no downstream routing')

    # Keep the clean generated seed before long native attempts. It is never
    # reconstructed from a stalled routed board or an old accepted checkpoint.
    seed = out / "prepared-seed"
    seed.mkdir()
    shutil.copytree(project, seed / "project")
    if existing_field:
        shutil.copy2(out/'matrix-field.json',seed/'matrix-field.json')
        write_manifest(seed/'project',generation_profile=PROFILE,proof_mode='controller-field')
    else:
        for name in ("controller-ports.json", "key-data-access.json", "controller-return-tree.json"):
            shutil.copy2(out / name, seed / name)
        write_manifest(seed / "project", generation_profile=PROFILE)

    phases = ordered_nets(cfg)
    selected = "ordered-native"
    native_error = None
    native_candidate = None
    global_error = None
    try:
        for phase, nets in phases:
            if not nets or phase in ('rows','columns'):
                continue
            for index, grid in enumerate((500000, 125000)):
                call("components", phase + "-" + str(index), include_nets=nets,
                     grid_step_nm=grid, connection_budget_s=3,
                     completion_search=index>0,
                     track_width_nm=control_width_nm if phase == "control" else 150000)
            call("verify", phase + "-connectivity")
        preplane=call("verify", "preplane-connectivity")
        protected=[uid for uid,binding in inventory(board).items() if binding["net"] in ("USB_DP","USB_DM")]
        recover_preplane_signals(project,out/"preplane-recovery",preplane,call,
            plane_nets=cfg["plane_nets"],protected_ids=protected,deadline=time.perf_counter()+200)
        call("planes", "planes")
        call("stitch", "stitch")
        independent = call("verify", "independent-native")
        checks = rule_checks(project, out / "ordered-native-checks", "keyboard", timeout_s=120)
        if (checks["erc"]["severity"] or checks["drc"]["severity"].get("error")
                or checks["drc"]["schematic_parity"]):
            raise ValueError("Native ordered attempt has physical or parity violations")
        native_candidate = (independent, checks)
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
                   sys.argv[1], tag, sys.argv[2] + "/prepared-seed",'--routing-quality',quality]
        print("checked global recovery from immutable generated seed", flush=True)
        try:
            with (out / "generated-global.log").open("w") as log:
                subprocess.run(command, cwd=out, env=environment(), stdout=log,
                               stderr=subprocess.STDOUT, timeout=1000, check=True)
        except subprocess.SubprocessError as error:
            # Only a physically checked native attempt is a usable fallback.
            # Its open groups remain explicit; completion/firmware still gate it.
            if native_candidate is None:
                raise
            global_error = str(error)
            selected = "ordered-native-after-global-failure"
            shutil.copytree(archive / "project", project)
            independent, checks = native_candidate
        else:
            global_out = base / tag
            admitted=True
            if native_candidate is not None:
                from smilepcb.keyboard_routing.handoff import preserves_contacts
                partitions=[]
                for label,candidate in (("native",archive/"project"),("global",global_out/"project")):
                    proof=worker(Path(__file__).with_name("inspect_exchange.py"),
                        {"operation":"inspect","board":str(candidate/"keyboard.kicad_pcb"),
                         "routing_quality":quality,"generation_profile":PROFILE},out/(label+"-pad-partitions.log"),timeout_s=90)
                    partitions.append(proof["pad_partitions"])
                    (out/(label+"-pad-partitions.json")).write_text(json.dumps(proof,indent=2)+"\n")
                admitted=preserves_contacts(*partitions)
                (out/"global-admission.json").write_text(json.dumps({"accepted":admitted,
                    "rule":"preserve every already-connected native pad group"},indent=2)+"\n")
            if admitted:
                shutil.copytree(global_out / "project", project)
                independent = json.loads((global_out / "independent.json").read_text())
                checks = json.loads((global_out / "native-checks/summary.json").read_text())
            else:
                selected="ordered-native-after-global-regression"
                global_error="Global recovery split previously connected physical pads"
                shutil.copytree(archive/"project",project)
                independent,checks=native_candidate
    # The cold entrypoint checks this standard location before native completion.
    (out / "native-checks").mkdir()
    (out / "native-checks/summary.json").write_text(json.dumps(checks, indent=2) + "\n")
    if file_hashes(source) != source_hashes:
        raise ValueError("Routing edited its placed source project")
    result = {"complete": False, "routing_scope": "completion/firmware gates pending",
              "selected_path": selected, "native_attempt_error": native_error,
              "global_attempt_error": global_error,
              "generation_profile": PROFILE,'routing_quality':quality, "elapsed_s": time.perf_counter() - started,
              "history": history, "phases": phases, "rgb_order": cfg["layout"]["rgb_order"],
              "independent": independent, "native_checks": checks,
              "source_unchanged": True, "hardware_tested": False}
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print("routing diagnostic", selected, "gaps", independent["pad_group_gaps"], flush=True)


if __name__ == "__main__":
    main()
