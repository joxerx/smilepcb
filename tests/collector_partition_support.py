"""Native metadata seams for the actual collector partition and scheduler."""
import ast
from collections import Counter, defaultdict
from copy import deepcopy
import heapq
from itertools import chain
import math
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


class Item:
    def __init__(self, net, uid, *, clearance=0, local=0, layers=(0, 2)):
        self.net, self.clearance, self.local, self.layers = net, clearance, local, layers
        self.m_Uuid = SimpleNamespace(AsString=lambda: uid)

    def GetNetname(self): return self.net
    def IsOnLayer(self, face): return face in self.layers
    def GetOwnClearance(self, face): return self.clearance
    def GetLocalClearance(self): return self.local
    def GetEffectiveShape(self, *args): raise AssertionError('Partition must not scan shapes')


class Board:
    def __init__(self, *, tracks=(), pads=(), forbid_scan=False, **rules):
        self.tracks, self.pads, self.scans = list(tracks), list(pads), []
        self.forbid_scan = forbid_scan
        self.rules = SimpleNamespace(m_TrackMinWidth=150000, m_MinClearance=150000,
                                     m_MinThroughDrill=300000, m_ViasMinSize=500000,
                                     m_ViasMinAnnularWidth=100000)
        vars(self.rules).update(rules)

    def GetDesignSettings(self): return self.rules

    def GetTracks(self):
        assert not self.forbid_scan, 'Compact cohort scanned copper'
        self.scans.append('tracks')
        return list(self.tracks)

    def GetFootprints(self):
        assert not self.forbid_scan, 'Compact cohort scanned pads'
        self.scans.append('pads')
        return [SimpleNamespace(Pads=lambda: list(self.pads))]


def rail_ports(nets, offsets, *, heading=(1, 0), origin=(0, 0), layer=0):
    tangent = (-heading[1], heading[0])
    result = []
    for net, offset in zip(nets, offsets):
        point = tuple(origin[i]+tangent[i]*offset for i in (0, 1))
        result.append(dict(net=net, exit_nm=point, exit_layer=layer,
                           heading_origin_nm=tuple(point[i]-heading[i] for i in (0, 1)),
                           added_ids=[net+'-head'], pad_ids=[net+'-pad'], terminal_id=net+'-head'))
    return result


def capacity(clock=None, *, profile_gap=173000):
    clock = clock or SimpleNamespace(monotonic=lambda: 0.)
    native = SimpleNamespace(F_Cu=0, B_Cu=2, PAD=type('Pad', (), {}), PAD_ATTRIB_NPTH=99,
                             FromMM=lambda mm: round(mm*1000000))
    ns = dict(math=math, time=clock, heapq=heapq, chain=chain, Counter=Counter, defaultdict=defaultdict,
              deepcopy=deepcopy, p=native,
              current_generation_profile=lambda: SimpleNamespace(foreign_copper_clearance_nm=profile_gap))
    # Both the arithmetic and native own-clearance semantics are production code.
    for relative in ('smilepcb/copper_obstacles.py', 'smilepcb/keyboard_routing/collector_capacity.py'):
        path = ROOT/relative
        definitions = [n for n in ast.parse(path.read_text()).body
                       if isinstance(n, (ast.FunctionDef, ast.ClassDef)) or
                       isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'DIRS' for t in n.targets)]
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec'), ns)
    return ns
