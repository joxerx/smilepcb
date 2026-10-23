"""KiCad 10 headless worker. stdin/stdout JSON only; no project discovery."""
if not __debug__:
    raise RuntimeError('SmilePCB validation requires Python without -O/PYTHONOPTIMIZE')

import json
import os
from pathlib import Path
import sys
import uuid

import pcbnew as p

NS = uuid.UUID("94cf9dd5-4f82-4c18-bef3-12ea4ec24ae1")
SUPPORT = Path(os.environ.get("SMILEPCB_KICAD_SUPPORT", "/Applications/KiCad/KiCad.app/Contents/SharedSupport"))


def identity(key):
    return str(uuid.uuid5(NS, key))


def point(xy):
    return p.VECTOR2I(round(xy[0] * p.FromMM(1)), round(xy[1] * p.FromMM(1)))


def xy(v):
    return [v.x, v.y]


def snapshot(board):
    tracks = {}
    for t in board.GetTracks():
        data = {"class": t.GetClass(), "start": xy(t.GetStart()), "end": xy(t.GetEnd()),
            "width": t.GetWidth(p.F_Cu) if isinstance(t, p.PCB_VIA) else t.GetWidth(),
            "layer": t.GetLayer(), "net": t.GetNetname(), "locked": t.IsLocked()}
        if isinstance(t, p.PCB_VIA):
            data.update(drill=t.GetDrillValue(), via_type=t.GetViaType(), top=t.TopLayer(), bottom=t.BottomLayer())
        if isinstance(t, p.PCB_ARC):
            data["mid"] = xy(t.GetMid())
        tracks[t.m_Uuid.AsString()] = data
    footprints = {}
    for f in board.GetFootprints():
        footprints[f.m_Uuid.AsString()] = {"ref": f.GetReference(), "value": f.GetValue(),
            "position": xy(f.GetPosition()), "rotation": f.GetOrientationDegrees(),
            "locked": f.IsLocked(), "path": f.GetPath().AsString(), "layer": f.GetLayer(),
            "pads": sorted([{"number": pad.GetNumber(), "net": pad.GetNetname(),
                "position": xy(pad.GetPosition()), "size": xy(pad.GetSize()),
                "shape": pad.GetShape(), "rotation": pad.GetOrientationDegrees(),
                "drill": xy(pad.GetDrillSize()), "drill_shape": pad.GetDrillShape(),
                "layers": pad.GetLayerSet().FmtHex(), "uuid": pad.m_Uuid.AsString()}
                for pad in f.Pads()], key=lambda x: (x["number"], x["uuid"]))}
    return {"tracks": tracks, "footprints": footprints}


def add_parts(board, parts, sheet=""):
    existing = {fp.GetReference() for fp in board.GetFootprints()}
    nets = {n.GetNetname(): n for _, n in board.GetNetsByNetcode().items()}
    for part in parts:
        if part["ref"] in existing:
            raise ValueError("Conflicting reference " + part["ref"])
        for name in part["nets"].values():
            if name and name not in nets:
                net = p.NETINFO_ITEM(board, name)
                board.Add(net)
                nets[name] = net
        library, name = part["footprint"].split(":")
        fp = p.FootprintLoad(str(SUPPORT / "footprints" / (library + ".pretty")), name)
        if fp is None:
            raise ValueError("Footprint not installed: " + part["footprint"])
        fp.SetFPID(p.LIB_ID(library, name))
        fp.SetReference(part["ref"])
        fp.SetValue(part["value"])
        for field, value in part.get("properties", {}).items():
            fp.SetField(field, value)
            fp.GetField(field).SetVisible(False)
        fp.SetPosition(point(part["pcb"]))
        if "reference_text" in part:
            label = part["reference_text"]
            fp.Reference().SetPosition(point([part["pcb"][i] + label["offset"][i] for i in (0, 1)]))
            fp.Reference().SetTextSize(point([.8, .8]))
            fp.Reference().SetTextThickness(p.FromMM(.12))
            fp.Reference().SetTextAngle(p.EDA_ANGLE(label["angle"], p.DEGREES_T))
        path = p.KIID_PATH()
        for component in ([sheet] if sheet else []) + [part["uuid"]]:
            path.push_back(p.KIID(component))
        fp.SetPath(path)
        for index, pad in enumerate(fp.Pads()):
            if part["nets"].get(pad.GetNumber()):
                pad.SetNet(nets[part["nets"][pad.GetNumber()]])
        board.Add(fp)
        existing.add(part["ref"])


def create_fixture(request):
    board = p.BOARD()
    add_parts(board, request["parts"])
    by_ref = {fp.GetReference(): fp for fp in board.GetFootprints()}
    by_ref["J1"].SetLocked(True)
    a = next(pad for pad in by_ref["SW1"].Pads() if pad.GetNumber() == "2")
    b = next(pad for pad in by_ref["D1"].Pads() if pad.GetNumber() == "2")
    middle = p.VECTOR2I(b.GetPosition().x, a.GetPosition().y)
    for index, (start, end) in enumerate([(b.GetPosition(), middle), (middle, a.GetPosition())]):
        track = p.PCB_TRACK(board)
        track.SetStart(start); track.SetEnd(end); track.SetWidth(p.FromMM(.25))
        track.SetLayer(p.F_Cu if index == 0 else p.B_Cu); track.SetNet(a.GetNet()); track.SetLocked(True)
        board.Add(track)
    # A via provides a second kind of existing copper object to preserve.
    via = p.PCB_VIA(board)
    via.SetPosition(middle); via.SetWidth(p.FromMM(.6)); via.SetDrill(p.FromMM(.3))
    via.SetViaType(p.VIATYPE_THROUGH); via.SetLayerPair(p.F_Cu, p.B_Cu)
    via.SetNet(a.GetNet()); via.SetLocked(True)
    board.Add(via)
    corners = [(5, 8), (90, 8), (90, 80), (5, 80)]
    for i in range(4):
        edge = p.PCB_SHAPE(board); edge.SetShape(p.SHAPE_T_SEGMENT)
        edge.SetStart(point(corners[i])); edge.SetEnd(point(corners[(i + 1) % 4]))
        edge.SetLayer(p.Edge_Cuts); edge.SetWidth(p.FromMM(.05))
        board.Add(edge)
    assert p.SaveBoard(request["board"], board)
    return snapshot(p.LoadBoard(request["board"]))


def main():
    if p.Version().split(".")[0] != "10":
        raise ValueError("Select a tested KiCad 10 runtime with SMILEPCB_KICAD_APP")
    request = json.load(sys.stdin)
    if request["action"] == "create":
        result = create_fixture(request)
    else:
        board = p.LoadBoard(request["board"])
        if request["action"] == "add":
            before = snapshot(board)
            add_parts(board, request["parts"], request["sheet"])
            assert p.SaveBoard(request["board"], board)
            reopened = p.LoadBoard(request["board"])
            after = snapshot(reopened)
            assert after["tracks"] == before["tracks"], "Manual routing changed"
            assert all(after["footprints"][key] == value for key, value in before["footprints"].items()), "Existing footprint changed"
            result = after
        elif request["action"] == "inspect":
            result = snapshot(board)
        else:
            raise ValueError("Unknown worker action")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
