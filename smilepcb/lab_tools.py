"""Offline native checks and bounded router invocation for synthetic lab workflows."""
from collections import Counter
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from .runtime import KICAD_CLI, KICAD_PYTHON, SUPPORT, environment
from .service import encode
NAME = 'SmilePCB transfer 3x4'
JAR_SHA256 = 'f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7'

def worker(script, request, log, *, timeout_s=90):
    if type(timeout_s) not in (int,float) or not 0<timeout_s<=90:
        raise ValueError('Native worker budget must be positive and at most 90 seconds')
    try:
        result = subprocess.run([KICAD_PYTHON, '-X', 'faulthandler', str(script)], input=json.dumps(request), text=True,
                                capture_output=True, env=environment(), timeout=timeout_s)
    except subprocess.TimeoutExpired as error:
        def decoded(value):return value.decode(errors='replace') if isinstance(value,bytes) else value or ''
        log.write_text(decoded(error.stdout)+decoded(error.stderr)+'\nNative worker deadline exhausted\n')
        raise
    log.write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f'Native worker exited {result.returncode}; see {log}\n{result.stderr}')
    return json.JSONDecoder().raw_decode(result.stdout)[0]


def export_netlist(project, output, name=NAME):
    subprocess.run([KICAD_CLI, "sch", "export", "netlist", "--format", "kicadxml", "-o", str(output),
                    str(project / (name + ".kicad_sch"))], check=True, capture_output=True, env=environment(), timeout=90)
    xml = ET.parse(output)
    pins = {}
    for net in xml.findall("./nets/net"):
        for node in net.findall("node"):
            pins[(node.attrib["ref"], node.attrib["pin"])] = net.attrib["name"]
    paths = {c.attrib["ref"]: c.find("sheetpath").attrib["tstamps"] + c.findtext("tstamps")
             for c in xml.findall("./components/comp")}
    fields = {c.attrib["ref"]: {f.attrib["name"]: f.text or "" for f in c.findall("./fields/field")
                                if f.attrib["name"].startswith("Sim.")}
              for c in xml.findall("./components/comp")}
    return pins, paths, fields


def scoped_footprint_table(project, board):
    """Load referenced fallback libraries while preserving project overrides.

    KiCad 10 waits for its library manager before starting DRC. Enumerating
    every installed library adds unrelated memory and startup cost. Missing
    libraries remain missing so native DRC can report them normally.
    """
    from . import schematic as s
    required={str(n[1]).split(':',1)[0] for n in s.children(s.read(board),'footprint')
              if ':' in str(n[1])}
    local=project/'fp-lib-table'
    local_rows=s.children(s.read(local),'lib') if local.exists() else []
    overrides={str(s.one(n,'name')[1]) for n in local_rows}
    table=s.read(SUPPORT/'template/fp-lib-table')
    table[:]=[n for n in table if s.tag(n)!='lib' or str(s.one(n,'name')[1]) in required-overrides]
    for row in local_rows:
        if str(s.one(row,'name')[1]) not in required:continue
        # This is an isolated global context, so resolve the project macro now.
        # The actual project's table remains unchanged and still takes priority.
        uri=s.one(row,'uri');uri[1]=str(uri[1]).replace('${KIPRJMOD}',str(project.resolve()))
        table.append(row)
    return s.sx.dumps(table)+'\n'


def rule_checks(project, destination, name=NAME, *, timeout_s=90, deadline=None):
    if type(timeout_s) not in (int,float) or not 0 < timeout_s <= 240:
        raise ValueError('Native check budget must be positive and at most 240 seconds')
    def remaining():
        if deadline is None:return timeout_s
        value=deadline-time.perf_counter()
        if not value>0:raise TimeoutError('Native check outer budget exhausted')
        return min(timeout_s,value)
    remaining()
    destination.mkdir()
    config = destination / "config/10.0"
    config.mkdir(parents=True)
    shutil.copy2(SUPPORT/'template/sym-lib-table',config/'sym-lib-table')
    (config/'fp-lib-table').write_text(scoped_footprint_table(project,project/(name+'.kicad_pcb')))
    env = environment(); env["KICAD_CONFIG_HOME"] = str(config.parent)
    summary = {}
    for domain, kind, suffix in (("sch", "erc", "kicad_sch"), ("pcb", "drc", "kicad_pcb")):
        target = destination / (kind + ".json")
        args = [KICAD_CLI, domain, kind, "--format", "json", "--severity-all", "--exit-code-violations", "-o", str(target)]
        if kind == "drc": args.append("--schematic-parity")
        try:
            result = subprocess.run([*args, str(project / (name + "." + suffix))], capture_output=True, text=True, env=env, timeout=remaining())
        except subprocess.TimeoutExpired as error:
            def decoded(value):return value.decode(errors='replace') if isinstance(value,bytes) else value or ''
            (destination / (kind + '.log')).write_text(decoded(error.stdout)+decoded(error.stderr)+'\nNative check deadline exhausted\n')
            raise
        (destination / (kind + ".log")).write_text(result.stdout + result.stderr)
        remaining()
        if result.returncode not in (0, 5): raise RuntimeError(result.stdout + result.stderr)
        data = json.loads(target.read_text())
        violations = [v for sheet in data["sheets"] for v in sheet["violations"]] if kind == "erc" else data["violations"]
        summary[kind] = {"exit_code": result.returncode, "severity": dict(Counter(v["severity"] for v in violations)),
                         "types": dict(Counter(v["type"] for v in violations)),
                         "schematic_parity": len(data.get("schematic_parity", [])),
                         "unconnected_items": len(data.get("unconnected_items", []))}
    (destination / "summary.json").write_text(encode(summary))
    return summary


def run_router(output, java, jar, *, ignore_classes=(), autorouter_passes=30, optimizer_passes=10, preferred_horizontal=None):
    """Same bounded, offline-file routing invocation for both synthetic probes."""
    if (type(autorouter_passes) is not int or type(optimizer_passes) is not int
            or not 1 <= autorouter_passes <= 30 or not 0 <= optimizer_passes <= 10):
        raise ValueError('Router pass budget must stay within the declared 30/10 limits')
    if preferred_horizontal is not None and (not isinstance(preferred_horizontal,(tuple,list))
            or len(preferred_horizontal)!=2 or any(type(v) is not bool for v in preferred_horizontal)):
        raise ValueError('Direction preferences require two explicit layer booleans')
    command = [str(java), "-Djava.awt.headless=true", "-Xmx512m", "-XX:ActiveProcessorCount=2", "-jar", str(jar),
        "-da", "--gui.enabled=false", "--api_server.enabled=false", "--mcp_server.enabled=false",
        "--router.autorouter.max_threads=2", "--router.optimizer.max_threads=2",
        "--router.autorouter.max_passes="+str(autorouter_passes), "--router.optimizer.max_passes="+str(optimizer_passes),
        "--router.automatic_neckdown=false", "--router.fanout.enabled=false", "--logging.file.enabled=false",
        "--user_data_path=" + str(output / "router-config"), "-de", str(output / "board.dsn"),
        "-do", str(output / "board.ses")]
    if ignore_classes:
        command.append("--router.autorouter.ignore_net_classes=" + ",".join(ignore_classes))
    if optimizer_passes == 0:
        command.append("--router.optimizer.enabled=false")
    if preferred_horizontal is not None:
        command.append('--router.layers.preferred_direction_horizontal='+','.join(str(v).lower() for v in preferred_horizontal))
    (output / "command.json").write_text(encode(command))
    route_started = time.perf_counter()
    with (output / "router.log").open("w") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        timer = threading.Timer(180, child.kill); timer.start()
        try:
            _, status, usage = os.wait4(child.pid, 0)
        except BaseException:
            child.kill()
            child.wait()
            raise
        finally:
            timer.cancel()
        child.returncode = os.waitstatus_to_exitcode(status)
    resources = {"exit_code": child.returncode, "elapsed_s": time.perf_counter() - route_started,
                 "peak_rss_bytes": usage.ru_maxrss * (1 if platform.system() == "Darwin" else 1024),
                 "user_cpu_s": usage.ru_utime, "system_cpu_s": usage.ru_stime,
                 "visible_processors": 2, "optimizer_threads": 2, "heap_limit_mib": 512}
    (output / "router-resource.json").write_text(encode(resources))
    if child.returncode or not (output / "board.ses").is_file():
        raise RuntimeError("Router did not produce a session; see router.log")
    return resources
