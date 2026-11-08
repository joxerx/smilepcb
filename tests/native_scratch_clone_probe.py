"""Check exact native rules and per-item clearances through a scratch load."""
import json
import tempfile
from pathlib import Path
from smilepcb.native_worker import p, snapshot
from smilepcb.copper_obstacles import own_clearance
from native_collector_flow_fixture import fixture


def rules(board):
    settings = board.GetDesignSettings()
    return {key: getattr(settings, key) for key in (
        'm_MinClearance', 'm_HoleClearance', 'm_TrackMinWidth',
        'm_CopperEdgeClearance', 'm_MinThroughDrill', 'm_ViasMinSize',
        'm_ViasMinAnnularWidth')}


def gaps(board):
    return {(pad.m_Uuid.AsString(), face): own_clearance(
                pad, face, board.GetDesignSettings().m_MinClearance)
            for footprint in board.GetFootprints() for pad in footprint.Pads()
            for face in (p.F_Cu, p.B_Cu) if pad.IsOnLayer(face)}


def differences(before, after):
    return {str(key): [value, after.get(key)] for key, value in before.items()
            if value != after.get(key)}


from smilepcb.keyboard_process import apply_native_rules
board, cfg = fixture(p.B_Cu)
apply_native_rules(board, 'fr4-2l-1oz-0p5-0p2')
pad = next(pad for footprint in board.GetFootprints() for pad in footprint.Pads())
pad.SetLocalClearance(411000)
before = snapshot(board)
before_rules = rules(board)
before_gaps = gaps(board)
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / 'scratch.kicad_pcb'
    assert p.SaveBoard(str(path), board)
    clone = p.LoadBoard(str(path))
    project = board.GetProject()
    if project is not None:
        clone.SetProject(project, True)
    clone.SetDesignSettings(board.GetDesignSettings())
    print(json.dumps({'gap_changes_after_settings_copy': differences(before_gaps, gaps(clone)),
                      'original_gap_changes': differences(before_gaps, gaps(board))}), flush=True)
    assert snapshot(clone) == before
    assert rules(clone) == before_rules
    assert gaps(clone) == before_gaps
    assert snapshot(board) == before and rules(board) == before_rules and gaps(board) == before_gaps
    clone.BuildConnectivity()
    print(json.dumps({'after_connectivity_gap_changes': differences(before_gaps, gaps(clone))}), flush=True)
    assert snapshot(clone) == before and gaps(clone) == before_gaps
    clone.GetDesignSettings().m_MinClearance = 333000
    assert rules(board) == before_rules, 'Design settings are aliased'
    assert gaps(board) == before_gaps, 'Original clearance semantics changed'
print(json.dumps({'scratch_rules_geometry_clearance_equal': True,
                  'settings_independent': True, 'project_present': project is not None,
                  'scratch_equivalence_survives_connectivity': True}))
