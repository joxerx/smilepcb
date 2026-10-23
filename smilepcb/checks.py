"""Native rule checks with isolated configuration and unmodified CAD inputs."""
from collections import Counter
import json
from pathlib import Path
import shutil
import subprocess

from .runtime import KICAD_CLI, SUPPORT, environment
from .service import NATIVE_LOCK, hashes, root_of


def check_rules(project, output):
    root = root_of(project)
    from . import encoder
    project_name = encoder.NAME if encoder.supports(root) else 'lab2x3'
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = output / "config/10.0"
    config.mkdir(parents=True)
    for name in ("sym-lib-table", "fp-lib-table"):
        shutil.copy2(SUPPORT / "template" / name, config / name)
    env = environment()
    env.update(KICAD_CONFIG_HOME=str(output / "config"), XDG_CACHE_HOME=str(output / "cache"))
    before = hashes(root)
    reports = {}
    for kind, domain, extension in (("erc", "sch", "kicad_sch"), ("drc", "pcb", "kicad_pcb")):
        target = output / f"{kind}.json"
        command = [KICAD_CLI, domain, kind, "--format", "json", "--severity-all", "--exit-code-violations",
                   "--output", str(target)]
        if kind == "drc":
            command.append("--schematic-parity")
        command.append(str(root / (project_name + '.' + extension)))
        with NATIVE_LOCK:
            done = subprocess.run(command, capture_output=True, text=True, env=env, timeout=90)
        (output / f"{kind}.log").write_text(done.stdout + done.stderr)
        if done.returncode not in (0, 5) or not target.is_file():
            raise ValueError(f"KiCad {kind.upper()} execution failed: " + done.stdout + done.stderr)
        report = json.loads(target.read_text())
        if kind == "erc":
            report["violations"] = [v for sheet in report["sheets"] for v in sheet["violations"]]
        report["counts"] = dict(Counter(v["severity"] for v in report["violations"]))
        reports[kind] = report
    if hashes(root) != before:
        raise ValueError("Rule checking unexpectedly modified a project input")
    reports["rule_checks_passed"] = not any((reports["erc"]["violations"], reports["drc"]["violations"],
                                            reports["drc"]["schematic_parity"], reports["drc"]["unconnected_items"]))
    reports["fabrication_ready"] = False  # Rule checks do not replace electrical/mechanical/hardware review.
    (output / "summary.json").write_text(json.dumps(reports, indent=2) + "\n")
    return reports
