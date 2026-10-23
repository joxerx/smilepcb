"""Acceptance checks for the fixed synthetic routing experiment."""
import math
import re


def adapt_dsn(text):
    if '(unit um)' not in text:
        raise ValueError("Only the fixed micron-unit KiCad export is supported")
    # Native save order follows random new via UUIDs. Canonicalize only their
    # serialization, never geometry: router item IDs affect heuristic choices.
    pattern = r'^    \(via "[^"\n]+"[^\n]*\(type fix\)\)$'
    vias = iter(sorted(re.findall(pattern, text, flags=re.M)))
    text = re.sub(pattern, lambda _: next(vias), text, flags=re.M)
    # KiCad expands these NPTH holes by 0.25 mm before export. Freerouting
    # then adds its normal 0.2 mm clearance. Remove only that double count:
    # diameter + 2*(0.25 - 0.2) retains the actual 0.25 mm hole clearance.
    pattern = r'(\(keepout "" \(circle (?:F|B)\.Cu )(2250|4500)(?=[ )])'
    matches = re.findall(pattern, text)
    if len(matches) != 6 or sum(d == '4500' for _, d in matches) != 2:
        raise ValueError("Unexpected fixed-fixture NPTH export geometry")
    text = re.sub(pattern, lambda m: m[1] + str(int(m[2]) - 400), text)
    if '(class USBPower +5V GND' in text:
        if text.count('(class USBPower ') != 1 or text.count('(width 600)') != 1 or text.count('(width 250)') != 2:
            raise ValueError("Unexpected native-exported power rules")
        return text
    header = '(class kicad_default +5V COL0 COL1 COL2 GND '
    if text.count(header) != 1 or text.count('(width 200)') != 2:
        raise ValueError("Unexpected fixed-fixture DSN rules")
    text = text.replace(header, '(class kicad_default COL0 COL1 COL2 ')
    text = text.replace('(width 200)', '(width 250)')
    anchor = '\n  )\n  (wiring'
    if text.count(anchor) != 1:
        raise ValueError("Unexpected fixed-fixture DSN network boundary")
    return text.replace(anchor, '\n    (class USBPower +5V GND '
        '(circuit (use_via "Via[0-1]_600:300_um")) (rule (width 600) (clearance 200)))' + anchor)


def verify_preservation(before, after, *, ground_only=False):
    assert after["footprints"] == before["footprints"], "Component identity, geometry or pad connections changed"
    for uid, segment in before["tracks"].items():
        assert after["tracks"].get(uid) == segment, "Original track changed: " + uid
    added = [t for uid, t in after["tracks"].items() if uid not in before["tracks"]]
    assert all(t["class"] in ("PCB_TRACK", "PCB_VIA") for t in added), "Unsupported routed geometry"
    if ground_only:
        assert all(t["class"] == "PCB_VIA" and t["net"] == "GND" for t in added), \
            "Router input contains a manually seeded signal route"
    return {"preserved_segments": sum(t["class"] != "PCB_VIA" for t in before["tracks"].values()),
            "preserved_vias": sum(t["class"] == "PCB_VIA" for t in before["tracks"].values()),
            "preserved_footprints": len(before["footprints"]),
            "added_segments": sum(t["class"] == "PCB_TRACK" for t in added),
            "added_vias": sum(t["class"] == "PCB_VIA" for t in added),
            "added_trace_length_mm": sum(math.dist(t["start"], t["end"]) / 1e6
                                         for t in added if t["class"] == "PCB_TRACK")}


def require_clean_rules(rules):
    """Enforce native acceptance even when Python assertions are disabled."""
    for name in ("erc", "drc"):
        result = rules[name]
        if (result["exit_code"] != 0 or result["severity"] or
                result["schematic_parity"] != 0 or result["unconnected_items"] != 0):
            raise ValueError(f"Native {name} is not clean: {result!r}")


def require_completed_native_case(case):
    """A missing/failed stage cannot be skipped by an overall success verdict."""
    if not isinstance(case,dict) or case.get('complete') is not True:
        raise ValueError('Native case did not complete')
    try:require_clean_rules(case['native_rules'])
    except (KeyError,TypeError) as error:
        raise ValueError('Completed case has missing native acceptance evidence') from error


def require_routable_rules(rules,report,*,stage):
    """Reject composition conflicts before routing; opens/cleanup may remain."""
    failures=[]
    erc=rules['erc'];drc=rules['drc']
    if (erc['exit_code'] or erc['severity'] or erc['schematic_parity'] or erc['unconnected_items']):
        failures.append('ERC: '+repr(erc))
    if drc['exit_code'] not in (0,5):failures.append('DRC tool failed: '+str(drc['exit_code']))
    if drc['schematic_parity'] or report['schematic_parity']:
        failures.append('Schematic/PCB parity: '+repr(report['schematic_parity'][:3]))
    for finding in report['violations']:
        if finding['type'] in ('track_dangling','via_dangling'):continue
        items='; '.join(item.get('description','')+' ['+item.get('uuid','')+']' for item in finding.get('items',[])[:4])
        failures.append(finding['type']+': '+finding['description']+'; '+items)
    if failures:
        raise ValueError('Native '+str(stage)+' conflict: '+' | '.join(failures[:6]))
