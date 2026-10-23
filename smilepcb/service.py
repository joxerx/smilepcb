"""Shared CLI/MCP service, restricted to explicitly generated lab projects."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET

from . import schematic
from .circuit import RGB_BLOCK, base_parts, fixture_config, qmk_config, rgb_led_position, rgb_parts, validate_config
from .runtime import BOARD_VERSION, KICAD_CLI, KICAD_PYTHON, environment

MARKER = "synthetic-smilepcb-lab-v1"
STATE = ".smilepcb-state.json"
FILES = (".smilepcb-lab", "keyboard.json", "lab2x3.kicad_sch", "lab2x3.kicad_pcb",
         "lab2x3.kicad_pro", "qmk/keyboard.json", "qmk/keymaps/default/keymap.c")
NATIVE_LOCK = threading.RLock()


def encode(data):
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def root_of(path):
    root = Path(path).resolve()
    from . import encoder
    if encoder.supports(root):
        return encoder.root_of(root)
    for name in (*FILES, "rgb.kicad_sch", STATE):
        file = root / name
        if file.is_symlink() or not file.resolve().is_relative_to(root):
            raise ValueError("Project symlinks are not supported")
    if not (root / ".smilepcb-lab").is_file() or (root / ".smilepcb-lab").read_text().strip() != MARKER:
        raise ValueError("Only explicitly generated SmilePCB lab projects are accepted")
    validate_schematic_sources(root, rgb=(root / STATE).exists())
    return root


def validate_schematic_sources(root, rgb):
    tree = schematic.read(root / "lab2x3.kicad_sch")
    sheets = schematic.children(tree, "sheet")
    if not rgb and sheets:
        raise ValueError("Unsupported child sheet in the synthetic carrier")
    if rgb:
        if len(sheets) != 1:
            raise ValueError("Expected exactly the generated RGB child sheet")
        file = next((prop[2] for prop in schematic.children(sheets[0], "property") if prop[1] == "Sheetfile"), None)
        if file != "rgb.kicad_sch":
            raise ValueError("Unsupported child sheet path")
        if schematic.children(schematic.read(root / file), "sheet"):
            raise ValueError("Nested RGB sheets are not supported")
    for name in ("sym-lib-table", "fp-lib-table"):
        if (root / name).exists():
            raise ValueError("External library tables are not supported by the lab")


def footprint_digests(board_path):
    """Cover all footprint fields, including texts/models and unknown attributes.

    Electrical net names are stable even if a native save renumbers net codes.
    """
    def normalize(node):
        if isinstance(node, list):
            if schematic.tag(node) == "net" and len(node) == 3:
                return [node[0], node[2]]
            return [normalize(item) for item in node]
        if isinstance(node, float) and node.is_integer():
            return int(node)
        return node
    result = {}
    for fp in schematic.children(schematic.read(board_path), "footprint"):
        identity = str((schematic.one(fp, "uuid") or schematic.one(fp, "tstamp"))[1])
        encoded = schematic.sx.dumps(normalize(fp)).encode()
        result[identity] = hashlib.sha256(encoded).hexdigest()
    return result


def check_qmk(config, actual, rgb):
    expected = qmk_config(config, rgb)
    for key in ("matrix_pins", "diode_direction", "processor", "bootloader", "layouts"):
        if actual.get(key) != expected[key]:
            raise ValueError("Generated QMK configuration was modified: " + key)
    if rgb and (any(actual.get(key) != expected[key] for key in ("ws2812", "rgb_matrix"))
                or actual.get("features", {}).get("rgb_matrix") is not True):
        raise ValueError("Owned QMK RGB configuration was modified")


def merge_rgb_qmk(config, existing):
    check_qmk(config, existing, False)
    if "ws2812" in existing or "rgb_matrix" in existing or existing.get("features", {}).get("rgb_matrix"):
        raise ValueError("Existing unowned QMK RGB configuration conflicts with the block")
    result = deepcopy(existing)
    expected = qmk_config(config, True)
    for key in ("ws2812", "rgb_matrix"):
        result[key] = expected[key]
    result.setdefault("features", {})["rgb_matrix"] = True
    return result


def native(action, board, **kwargs):
    if action != "create":
        with Path(board).open("rb") as handle:
            header = handle.read(1024)
        version = re.search(rb"\(\s*kicad_pcb\s+\(version\s+(\d+)\)", header)
        if not version or int(version[1]) != BOARD_VERSION:
            raise ValueError("Unsupported PCB format: use the tested KiCad 10 format in this lab")
    request = dict(action=action, board=str(board), **kwargs)
    with NATIVE_LOCK:
        done = subprocess.run([KICAD_PYTHON, str(Path(__file__).with_name("native_worker.py"))],
            input=json.dumps(request), text=True, capture_output=True, timeout=60, env=environment())
    if done.returncode:
        raise ValueError("KiCad worker failed: " + done.stderr.strip())
    return json.loads(done.stdout.strip().splitlines()[-1])


def hashes(root):
    from . import encoder
    if encoder.supports(root):
        return encoder.input_hashes(root)
    return {name: sha(root / name) for name in (*FILES, "rgb.kicad_sch", STATE) if (root / name).is_file()}


def create_fixture(path):
    root = Path(path)
    if root.exists() and any(root.iterdir()):
        raise ValueError("Fixture destination must be empty")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    config = fixture_config()
    (root / ".smilepcb-lab").write_text(MARKER + "\n")
    (root / "keyboard.json").write_text(encode(config))
    (root / "lab2x3.kicad_pro").write_text(encode({"meta": {"version": 1, "filename": "lab2x3.kicad_pro"}}))
    schematic.create_base(root / "lab2x3.kicad_sch", base_parts(config))
    result = native("create", root / "lab2x3.kicad_pcb", parts=base_parts(config))
    (root / "qmk/keymaps/default").mkdir(parents=True)
    (root / "qmk/keyboard.json").write_text(encode(qmk_config(config)))
    (root / "qmk/keymaps/default/keymap.c").write_text(
        '#include QMK_KEYBOARD_H\nconst uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {\n'
        '    [0] = LAYOUT(KC_A, KC_B, KC_C, KC_D, KC_E, KC_F)\n};\n')
    return result


def inspect_project(path):
    from . import encoder
    if encoder.supports(path):
        return encoder.inspect_encoder(path)
    root = root_of(path)
    return {"config": json.loads((root / "keyboard.json").read_text()),
            "hashes": hashes(root), "board": native("inspect", root / "lab2x3.kicad_pcb"),
            "rgb_installed": (root / STATE).exists()}


def plan_rgb(path):
    root = root_of(path)
    config = json.loads((root / "keyboard.json").read_text())
    power = validate_config(config)
    if (root / STATE).exists():
        verify_project(root)
        return {"status": "already_applied", "block": RGB_BLOCK}
    if (root / "rgb.kicad_sch").exists():
        raise ValueError("An unowned rgb.kicad_sch already exists")
    merge_rgb_qmk(config, json.loads((root / "qmk/keyboard.json").read_text()))
    additions = rgb_parts(config)
    board = native("inspect", root / "lab2x3.kicad_pcb")
    refs = {f["ref"] for f in board["footprints"].values()}
    if refs & {p["ref"] for p in additions}:
        raise ValueError("RGB references conflict with existing footprints")
    return {"status": "ready", "block": RGB_BLOCK, "source_hashes": hashes(root),
            "additions": additions, "power": power,
            "limitations": ["RGB connections remain unrouted", "No hardware or ERC/DRC release validation"]}


def schematic_pin_nets(root):
    with tempfile.TemporaryDirectory(prefix=".smilepcb-netlist-", dir=root) as temp:
        target = Path(temp) / "netlist.xml"
        done = subprocess.run([KICAD_CLI, "sch", "export", "netlist", "--format", "kicadxml", "-o", str(target),
            str(root / "lab2x3.kicad_sch")], text=True, capture_output=True, timeout=60, env=environment())
        if done.returncode:
            raise ValueError("KiCad netlist export failed: " + done.stderr)
        xml = ET.parse(target)
    pins = {}
    paths = {}
    for comp in xml.findall("./components/comp"):
        paths[comp.attrib["ref"]] = comp.find("sheetpath").attrib["tstamps"] + comp.findtext("tstamps")
    for net in xml.findall("./nets/net"):
        for node in net.findall("node"):
            pins[(node.attrib["ref"], node.attrib["pin"])] = net.attrib["name"]
    return pins, paths


def check_project(root, config, board, rgb=True):
    parts = base_parts(config) + (rgb_parts(config) if rgb else [])
    actual = {fp["ref"]: fp for fp in board["footprints"].values()}
    if len(actual) != len(parts):
        raise ValueError("Schematic/PCB component inventory mismatch")
    pins, paths = schematic_pin_nets(root)
    expected_pins = {(part["ref"], pin): net for part in parts for pin, net in part["nets"].items()}
    if pins != expected_pins:
        missing = {str(k): [expected_pins.get(k), pins.get(k)] for k in expected_pins.keys() | pins.keys() if expected_pins.get(k) != pins.get(k)}
        raise ValueError("Schematic pin net mismatch: " + json.dumps(missing))
    for part in parts:
        fp = actual[part["ref"]]
        board_pins = {pad["number"]: pad["net"] for pad in fp["pads"] if pad["number"]}
        if board_pins != part["nets"] or fp["path"] != paths[part["ref"]]:
            raise ValueError("PCB pad nets or schematic instance path mismatch for " + part["ref"])
    qmk = json.loads((root / "qmk/keyboard.json").read_text())
    check_qmk(config, qmk, rgb)
    if rgb:
        for index, led in enumerate(qmk["rgb_matrix"]["layout"]):
            key = config["keys"][index]
            if led["matrix"] != [key["row"], key["col"]] or actual[f"LED{index+1}"]["position"] != [round(v*1e6) for v in rgb_led_position(key)]:
                raise ValueError("Firmware LED order differs from PCB placement")
    return {"schematic_pcb_pin_nets_match": True, "qmk_chain_order_matches": True,
            "led_count": len(config["keys"]) if rgb else 0,
            "components": len(parts), "pin_net_assignments": len(pins),
            "new_rgb_connections_routed": False, "erc_drc_passed": None, "hardware_tested": False}


def verify_project(path):
    from . import encoder
    if encoder.supports(path):
        return encoder.verify_encoder(path)
    root = root_of(path)
    config = json.loads((root / "keyboard.json").read_text())
    validate_config(config)
    board = native("inspect", root / "lab2x3.kicad_pcb")
    state = json.loads((root / STATE).read_text()) if (root / STATE).exists() else None
    if state:
        if sha(root / "rgb.kicad_sch") != state["rgb_sheet_sha256"]:
            raise ValueError("Owned RGB schematic was modified")
        if any(board["footprints"].get(k) != value for k, value in state["owned_footprints"].items()):
            raise ValueError("Owned RGB footprint was modified")
        current = footprint_digests(root / "lab2x3.kicad_pcb")
        if any(current.get(k) != value for k, value in state["owned_footprint_digests"].items()):
            raise ValueError("Owned RGB footprint content was modified")
    result = check_project(root, config, board, rgb=state is not None)
    result["preserved_tracks"] = state["preserved_tracks_at_apply"] if state else len(board["tracks"])
    return result


@contextmanager
def lock(root):
    target = root / ".apply.lock"
    fd = None
    # Hold caught cancellation signals until acquisition is inside the cleanup
    # guard. In particular, do not lose a successful O_EXCL before storing fd.
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
    try:
        try:
            try:
                fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise ValueError("An apply operation is already active; inspect a stale .apply.lock before removing it")
            os.close(fd)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        yield
    finally:
        if fd is not None:
            target.unlink(missing_ok=True)


def commit_files(root, staged, names, *, delete_names=(), verify=None):
    originals = {name: (root / name).read_bytes() if (root / name).exists() else None for name in names}
    changed = []
    created_directories = set()
    try:
        for name in names:
            directory = (root / name).parent
            while directory != root and not directory.exists():
                created_directories.add(directory)
                directory = directory.parent
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            changed.append(name)
            if name in delete_names:
                (root / name).unlink(missing_ok=True)
            else:
                os.replace(staged / name, root / name)
        if verify is not None:
            verify()
    except BaseException:
        for name in reversed(changed):
            if originals[name] is None:
                (root / name).unlink(missing_ok=True)
            else:
                backup = staged / (name + ".rollback")
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(originals[name])
                os.replace(backup, root / name)
        for directory in sorted(created_directories, key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def apply_plan(path, plan):
    from . import encoder
    if plan.get('block') == encoder.BLOCK:
        return encoder.apply_encoder(path, plan)
    root = root_of(path)
    with lock(root):
        if (root / STATE).exists():
            return {"changed": False, "verification": verify_project(root)}
        if plan.get("source_hashes") != hashes(root):
            raise ValueError("Project changed after planning; create a fresh plan")
        if plan != plan_rgb(root):
            raise ValueError("Plan contents changed or are not supported")
        config = json.loads((root / "keyboard.json").read_text())
        before = native("inspect", root / "lab2x3.kicad_pcb")
        before_digests = footprint_digests(root / "lab2x3.kicad_pcb")
        with tempfile.TemporaryDirectory(prefix=".smilepcb-stage-", dir=root) as temp:
            staged = Path(temp)
            for name in FILES:
                (staged / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / name, staged / name)
            schematic.add_rgb(staged / "lab2x3.kicad_sch", staged / "rgb.kicad_sch", plan["additions"])
            after = native("add", staged / "lab2x3.kicad_pcb", parts=plan["additions"], sheet=schematic.RGB_ID)
            if after["tracks"] != before["tracks"] or any(after["footprints"].get(k) != v for k, v in before["footprints"].items()):
                raise ValueError("Existing manual work changed during staging")
            after_digests = footprint_digests(staged / "lab2x3.kicad_pcb")
            if any(after_digests.get(k) != value for k, value in before_digests.items()):
                raise ValueError("Existing footprint content changed during staging")
            original_qmk = json.loads((root / "qmk/keyboard.json").read_text())
            (staged / "qmk/keyboard.json").write_text(encode(merge_rgb_qmk(config, original_qmk)))
            validate_schematic_sources(staged, rgb=True)
            proof = check_project(staged, config, after)
            state = {"block": RGB_BLOCK, "rgb_sheet_sha256": sha(staged / "rgb.kicad_sch"),
                "owned_footprints": {k: v for k, v in after["footprints"].items() if k not in before["footprints"]},
                "owned_footprint_digests": {k: v for k, v in after_digests.items() if k not in before_digests},
                "preserved_tracks_at_apply": len(before["tracks"])}
            (staged / STATE).write_text(encode(state))
            if hashes(root) != plan["source_hashes"]:
                raise ValueError("Project changed during staging")
            commit_files(root, staged, ["lab2x3.kicad_sch", "rgb.kicad_sch", "lab2x3.kicad_pcb", "qmk/keyboard.json", STATE])
        return {"changed": True, "verification": proof, "preserved_tracks": len(before["tracks"])}
