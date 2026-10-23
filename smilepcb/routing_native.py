"""Native KiCad bridge for routing the explicit synthetic 2x3 fixture only."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smilepcb import native_worker as worker

p = worker.p


def replace_unlocked_copper(board, imported):
    """Accept a full reroute without importing exchange-rounded placement.

    Callers own authorization to replace generated copper. Locked native
    objects and unknown nets fail before any board mutation.
    """
    existing = list(board.GetTracks())
    if board is imported or any(t.IsLocked() for t in existing):
        raise ValueError('Full copper replacement requires a separate result and unlocked source')
    copies = []
    for track in imported.GetTracks():
        net = board.FindNet(track.GetNetname())
        if not net or not track.GetNetname():
            raise ValueError('Imported copper has an unknown or empty net')
        copy = track.Duplicate()
        copy.SetNet(net)
        copy.SetLocked(False)
        copies.append(copy)
    for track in existing:
        board.Delete(track)
    for track in copies:
        board.Add(track)
    board.BuildConnectivity()
    return {'removed': len(existing), 'imported': len(copies)}


def run(request, *, validator=None):
    board = p.LoadBoard(request["board"])
    key_count = request.get("key_count", 6)
    if validator is not None:
        validator(board, request)
    elif request.get('encoder_fixture'):
        assert key_count in (6, 12), "Only the selected synthetic transfer cases are supported"
        from smilepcb.encoder_recipe import validate_encoder_fixture
        validate_encoder_fixture([{'ref': f.GetReference(), 'footprint': f.GetFPID().GetUniStringLibId()}
                                  for f in board.GetFootprints()], key_count)
    else:
        assert key_count in (6, 12), "Only the selected synthetic transfer cases are supported"
        assert len(board.GetFootprints()) == 4 * key_count + 5
        assert {f.GetReference() for f in board.GetFootprints()} >= {"U_MCU", "U_RGB", f"LED{key_count}"}
    before = worker.snapshot(board)
    if request["operation"] == "thermal_context":
        modes = (p.ZONE_CONNECTION_THERMAL, p.ZONE_CONNECTION_THT_THERMAL)
        eligible = []
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                mode = pad.GetLocalZoneConnection()
                if mode == p.ZONE_CONNECTION_INHERITED:
                    mode = fp.GetLocalZoneConnection()
                if pad.GetAttribute() == p.PAD_ATTRIB_PTH and mode in (*modes, p.ZONE_CONNECTION_INHERITED):
                    eligible.append(pad.m_Uuid.AsString())
        return {"eligible_pads": sorted(eligible), "zones": {
            z.m_Uuid.AsString(): z.GetNetname() for z in board.Zones() if z.GetPadConnection() in modes}}
    if request["operation"] == "rotate_thermals":
        selected = set(request["pad_uuids"])
        changes = []
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                uid = pad.m_Uuid.AsString()
                if uid not in selected:
                    continue
                assert pad.GetAttribute() == p.PAD_ATTRIB_PTH
                angle = pad.GetThermalSpokeAngleDegrees()
                assert angle in (0, 45), "Only orthogonal/diagonal thermal orientations are supported"
                gap, width = pad.GetLocalThermalGapOverride(), pad.GetLocalThermalSpokeWidthOverride()
                pad.SetThermalSpokeAngleDegrees((angle + 45) % 90)
                assert (gap, width) == (pad.GetLocalThermalGapOverride(), pad.GetLocalThermalSpokeWidthOverride())
                changes.append({"uuid": uid, "ref": fp.GetReference(), "pad": pad.GetNumber(),
                                "old_angle": angle, "new_angle": pad.GetThermalSpokeAngleDegrees(),
                                "local_gap_override_iu": gap, "local_spoke_width_override_iu": width})
        assert len(changes) == len(selected)
        p.ZONE_FILLER(board).Fill(board.Zones())
        assert worker.snapshot(board) == before, "Thermal rotation changed recorded copper or pad geometry"
        assert p.SaveBoard(request["board"], board)
        return {"thermal_rotations": changes}
    if request["operation"] == "reset_to_seed":
        from smilepcb.routing_policy import reset_candidates
        seed = request["seed"]
        remove = set(reset_candidates(seed, before))
        for track in list(board.GetTracks()):
            if track.m_Uuid.AsString() in remove:
                board.Delete(track)  # Permanent deletion; Remove transfers ownership to SWIG GC.
        board.BuildConnectivity()
        p.ZONE_FILLER(board).Fill(board.Zones())
        assert p.SaveBoard(request["board"], board)
        after = worker.snapshot(board)
        assert after == seed, "Reset did not restore the protected seed"
        return after
    if request["operation"] == "ground_planes":
        for layer in (p.F_Cu, p.B_Cu):
            zone = p.ZONE(board)
            zone.SetLayer(layer)
            zone.SetNetCode(board.FindNet("GND").GetNetCode())
            zone.SetLocalClearance(p.FromMM(.2))
            zone.SetMinThickness(p.FromMM(.2))
            zone.SetPadConnection(p.ZONE_CONNECTION_THT_THERMAL)
            zone.SetThermalReliefGap(p.FromMM(.25))
            zone.SetThermalReliefSpokeWidth(p.FromMM(.3))
            zone.SetIslandRemovalMode(p.ISLAND_REMOVAL_MODE_ALWAYS)
            outline = zone.Outline(); outline.NewOutline()
            for x, y in request.get("ground_outline", [(-46.25, -16.25), (48.25, -16.25), (48.25, 43.25), (-46.25, 43.25)]):
                outline.Append(p.FromMM(x), p.FromMM(y))
            board.Add(zone)
        footprints = {f.GetReference(): f for f in board.GetFootprints()}
        anchors = [(f"C_LED{i}", "2", [0, -.9]) for i in range(1, key_count + 1)]
        anchors += [(f"LED{i}", "3", [1.85, 0]) for i in range(1, key_count + 1)]
        anchors += [("U_RGB", "1", [-1.5, 0]), ("U_RGB", "3", [-1.5, 0])]
        for ref, number, offset in anchors:
            pad = next(pad for pad in footprints[ref].Pads() if pad.GetNumber() == number)
            assert pad.GetNetname() == "GND"
            via = p.PCB_VIA(board)
            via.SetPosition(pad.GetPosition() + worker.point(offset))
            via.SetWidth(p.FromMM(.6)); via.SetDrill(p.FromMM(.3))
            via.SetViaType(p.VIATYPE_THROUGH); via.SetLayerPair(p.F_Cu, p.B_Cu)
            via.SetNetCode(pad.GetNetCode())
            board.Add(via)
        board.BuildConnectivity()
        p.ZONE_FILLER(board).Fill(board.Zones())
        assert p.SaveBoard(request["board"], board)
        return worker.snapshot(p.LoadBoard(request["board"]))
    if request["operation"] == "export":
        for track in board.GetTracks():
            track.SetLocked(True)  # In-memory export only; source lock flags stay unchanged.
        assert p.ExportSpecctraDSN(board, request["dsn"])
        return {"protected_copper_objects": len(before["tracks"])}
    if request["operation"] == "import":
        imported = p.LoadBoard(request["board"])
        # Read SES copper in isolation: retained locked originals must not
        # falsely prove that the router returned a temporarily splittable net.
        for track in list(imported.GetTracks()):
            imported.Delete(track)
        assert p.ImportSpecctraSES(imported, request["ses"])
        imported_snapshot = worker.snapshot(imported)
        from smilepcb.routing_copper import routing_delta
        returned = dict(imported_snapshot["tracks"])
        junction_nets = set(request.get("junction_nets", []))
        # SYSTEM_FIXED copper is deliberately absent from SES. Only those
        # explicit omissions may borrow source geometry; splittable nets must
        # be fully covered by the session itself.
        for uid, item in before["tracks"].items():
            if item["net"] not in junction_nets or item["class"] == "PCB_VIA":
                returned["fixed-source-" + uid] = item
        delta = routing_delta(before["tracks"], returned)
        by_id = {t.m_Uuid.AsString(): t for t in imported.GetTracks()}
        for uid, shape in delta:
            copy = by_id[uid].Duplicate()
            copy.SetNetCode(board.FindNet(shape["net"]).GetNetCode())
            if shape["class"] == "PCB_TRACK":
                copy.SetStart(p.VECTOR2I(*shape["start"]))
                copy.SetEnd(p.VECTOR2I(*shape["end"]))
            copy.SetLocked(False)
            board.Add(copy)
        board.BuildConnectivity()
        p.ZONE_FILLER(board).Fill(board.Zones())
        assert p.SaveBoard(request["board"], board)
        return worker.snapshot(p.LoadBoard(request["board"]))
    raise ValueError("Unknown fixed-fixture operation")


if __name__ == "__main__":
    print(json.dumps(run(json.load(sys.stdin))))
