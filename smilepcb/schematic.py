"""Schematic composition using the selected KiCad installation's standard symbols."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import sexpdata as sx

from .circuit import identity, part
from .runtime import SUPPORT

S = sx.Symbol
ROOT_ID = identity("sheet/root")
RGB_ID = identity("sheet/rgb")
PORTS = ("+5V", "GND", "RGB_DATA")


def tag(n):
    return str(n[0]) if isinstance(n, list) and n else ""


def children(n, kind):
    return [item for item in n if tag(item) == kind]


def one(n, kind):
    return next((item for item in n if tag(item) == kind), None)


def read(path):
    return sx.loads(Path(path).read_text())


def write(path, tree):
    Path(path).write_text(sx.dumps(tree, pretty_print=True) + "\n")


@lru_cache(maxsize=16)
def library(name):
    return read(SUPPORT / "symbols" / (name + ".kicad_sym"))


@lru_cache(maxsize=32)
def symbol(lib_id):
    lib, name = lib_id.split(":")
    source = next(s for s in children(library(lib), "symbol") if s[1] == name)
    if one(source, "extends"):
        base_name = one(source, "extends")[1]
        base = symbol(lib + ":" + base_name)
        result = deepcopy(base)
        # Resolve symbol inheritance into a standalone embedded symbol.
        for nested in children(result, "symbol"):
            nested[1] = nested[1].replace(base_name + "_", name + "_", 1)
        result = [n for n in result if tag(n) != "property"]
        result.extend(deepcopy(children(source, "property")))
    else:
        result = deepcopy(source)
    result[1] = lib_id
    return result


def effects(size=1.0):
    return [S("effects"), [S("font"), [S("size"), size, size]]]


def snap(value):
    return round(round(value / 1.27) * 1.27, 6)


def label(kind, net, x, y, key):
    result = [S(kind), net]
    if kind in ("global_label", "hierarchical_label"):
        result.append([S("shape"), S("input")])
    result.extend([[S("at"), x, y, 0], effects(), [S("uuid"), S(identity(key))]])
    return result


def add_parts(tree, parts, sheet_path, child=False, *, symbols=None, outward_labels=False):
    libraries = one(tree, "lib_symbols")
    existing = {s[1] for s in children(libraries, "symbol")}
    for part in parts:
        embedded = symbols[part["symbol"]] if symbols and part["symbol"] in symbols else symbol(part["symbol"])
        if part["symbol"] not in existing:
            libraries.append(deepcopy(embedded))
            existing.add(part["symbol"])
        x, y = map(snap, part["sch"])
        ref = part["ref"]
        physical = S("no") if ref.startswith("#") else S("yes")
        instance = [S("symbol"), [S("lib_id"), part["symbol"]], [S("at"), x, y, 0],
            [S("unit"), 1], [S("in_bom"), physical], [S("on_board"), physical],
            [S("dnp"), S("no")], [S("uuid"), S(part["uuid"])]]
        offsets={"Reference":-7,"Value":7,"Footprint":9}
        if outward_labels:
            from .schematic_layout import property_offsets
            offsets=property_offsets(part,embedded)
        for name, value, dy in [("Reference", ref, offsets['Reference']), ("Value", part["value"], offsets['Value']),
                                ("Footprint", part["footprint"], offsets['Footprint'])]:
            prop = [S("property"), name, value, [S("at"), x, y + dy, 0], effects()]
            if name == "Footprint":
                prop[-1].append(S("hide"))
            instance.append(prop)
        for name, value in part.get("properties", {}).items():
            if name in ("Reference", "Value", "Footprint"):
                raise ValueError("Component properties cannot replace standard identity fields")
            instance.append([S("property"), name, value, [S("at"), x, y, 0],
                             [*effects(), S("hide")]])
        pins = [pin for unit in children(embedded, "symbol") for pin in children(unit, "pin")]
        for pin in pins:
            number = str(one(pin, "number")[1])
            instance.append([S("pin"), number, [S("uuid"), S(identity("pin/" + ref + "/" + number))]])
            if number not in part["nets"]:
                continue
            net = part["nets"][number]
            at = one(pin, "at")
            px, py = round(x + at[1], 6), round(y - at[2], 6)
            if number in part.get("no_connect", []):
                tree.append([S("no_connect"), [S("at"), px, py],
                             [S("uuid"), S(identity("nc/" + ref + "/" + number))]])
                continue
            kind = "hierarchical_label" if child and net in PORTS else "global_label"
            label_x,label_y,label_angle=px,py,0
            if outward_labels:
                from .schematic_layout import pin_exit
                ex,ey,label_angle=pin_exit(pin)
                label_x,label_y=round(x+ex,6),round(y+ey,6)
                tree.append([S('wire'),[S('pts'),[S('xy'),px,py],[S('xy'),label_x,label_y]],
                             [S('stroke'),[S('width'),0],[S('type'),S('default')]],
                             [S('uuid'),S(identity('wire/'+ref+'/'+number))]])
            node=label(kind, net, label_x, label_y, "label/" + ref + "/" + number)
            one(node,'at')[3]=label_angle
            if outward_labels:
                one(node,'effects').append([S('justify'),S('left' if label_angle in (0,90) else 'right')])
            tree.append(node)
        instance.append([S("instances"), [S("project"), "lab2x3",
            [S("path"), sheet_path, [S("reference"), ref], [S("unit"), 1]]]])
        tree.append(instance)


def empty(root_id):
    return [S("kicad_sch"), [S("version"), 20230121], [S("generator"), S("eeschema")],
            [S("uuid"), S(root_id)], [S("paper"), "A4"], [S("lib_symbols")]]


def create_base(path, parts):
    tree = empty(ROOT_ID)
    add_parts(tree, parts, "/" + ROOT_ID)
    flags = [part(f"#FLG0{index}", "power:PWR_FLAG", "", {"1": net}, (0, 0), (20 + index*15, 100))
             for index, net in enumerate(("+5V", "GND"), 1)]
    add_parts(tree, flags, "/" + ROOT_ID)
    write(path, tree)


def add_rgb(root_path, child_path, parts):
    root = read(root_path)
    if any(str(one(n, "uuid")[1]) == RGB_ID for n in children(root, "sheet")):
        raise ValueError("RGB sheet already exists")
    child = empty(identity("file/rgb"))
    add_parts(child, parts, "/" + ROOT_ID + "/" + RGB_ID, child=True)
    sheet = [S("sheet"), [S("at"), snap(30), snap(130)], [S("size"), snap(60), snap(30)],
        [S("stroke"), [S("width"), .1524], [S("type"), S("solid")]],
        [S("fill"), [S("color"), 0, 0, 0, 0]], [S("uuid"), S(RGB_ID)],
        [S("property"), "Sheetname", "RGB", [S("at"), 30, 129, 0], effects()],
        [S("property"), "Sheetfile", "rgb.kicad_sch", [S("at"), 30, 161, 0], effects()]]
    for index, net in enumerate(PORTS):
        x, y = snap(30), snap(135 + index * 7.62)
        sheet.append([S("pin"), net, S("input"), [S("at"), x, y, 180], effects(),
                      [S("uuid"), S(identity("sheet-pin/" + net))]])
        root.append(label("global_label", net, x, y, "sheet-label/" + net))
    sheet.append([S("instances"), [S("project"), "lab2x3", [S("path"), "/" + ROOT_ID, [S("page"), "2"]]]])
    root.append(sheet)
    write(root_path, root)
    write(child_path, child)
