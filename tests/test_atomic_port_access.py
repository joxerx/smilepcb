"""Independent behavioral regression: source-cap clearance before lattice entry.

Run from the SmilePCB checkout:
    PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests/test_atomic_port_access.py

Uses production grid_search and PortPolicy methods, but never imports KiCad.
The synthetic collision graph has one physically legal L-shaped path. Track
capsules use exact segment-distance geometry. This is a kernel regression,
not a native-board qualification.
"""
import ast
import importlib.util
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def point_segment_distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    denominator = dx * dx + dy * dy
    t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / denominator if denominator else 0
    t = max(0, min(1, t))
    return math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)


def segment_distance(a, b, c, d):
    cross = lambda x, y: x[0] * y[1] - x[1] * y[0]
    u, v, z = ((b[0] - a[0], b[1] - a[1]),
               (d[0] - c[0], d[1] - c[1]),
               (c[0] - a[0], c[1] - a[1]))
    denominator = cross(u, v)
    if denominator and 0 <= cross(z, v) / denominator <= 1 and 0 <= cross(z, u) / denominator <= 1:
        return 0
    return min(point_segment_distance(a, c, d), point_segment_distance(b, c, d),
               point_segment_distance(c, a, b), point_segment_distance(d, a, b))


class SourceTrack:
    def IsOnLayer(self, layer):
        return layer == 0

    def GetWidth(self):
        return 150000

    def GetClass(self):
        return 'PCB_TRACK'

    def GetEffectiveShape(self, layer):
        return ((-1000000, 0), (0, 0), self.GetWidth())


class Via:
    pass


def load_production(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    assert (root / 'smilepcb/keyboard_routing/grid_search.py').is_file(), 'Run from the SmilePCB checkout'
    native = SimpleNamespace(
        F_Cu=0, B_Cu=2, PCB_TRACK=SourceTrack, PCB_VIA=Via,
        FromMM=lambda mm: round(mm * 1000000),
        SHAPE_SEGMENT=lambda a, b, width: (a, b, width),
        SHAPE=SimpleNamespace(Collide=lambda x, y, gap:
            segment_distance(x[0], x[1], y[0], y[1]) <= (x[2] + y[2]) / 2 + gap),
    )
    monkeypatch.setitem(sys.modules, 'smilepcb.native_worker', SimpleNamespace(p=native))
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.local_router',
                        SimpleNamespace(vec=lambda point: point, distance=math.dist))
    spec = importlib.util.spec_from_file_location(
        'smilepcb.keyboard_routing._atomic_access_regression',
        root / 'smilepcb/keyboard_routing/grid_search.py')
    grid = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(grid)

    # Load the entire current production class, including any new access-radius
    # method. Bypass its native board-indexing constructor only.
    path = root / 'smilepcb/keyboard_routing/port_policy.py'
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PortPolicy')
    namespace = {'p': native, 'math': math, 'vec': lambda point: point,
                 'terminal_origin': lambda port: tuple(port['heading_origin_nm'])}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    policy_type = namespace['PortPolicy']
    policy = object.__new__(policy_type)
    port = {'net': 'signal', 'exit_nm': (0, 0), 'heading_origin_nm': (-1000000, 0),
            'exit_layer': 0, 'terminal_id': 'source-terminal',
            'added_ids': ['source-terminal'], 'pad_ids': []}
    policy.track_width = 150000
    policy.ports = [port]
    policy.by_net = {'signal': [port]}
    policy.items = {'source-terminal': SourceTrack()}
    policy._launch_conflict = lambda *args: False
    policy.accepts_via = lambda *args: False
    return grid, policy


class CorridorRouter:
    deadline = None
    drill = 200000
    via_width = 500000
    track_width = 150000
    search_budget_s = 1
    search_expansion_limit = 5000
    generation = SimpleNamespace(name='dsn-safe-v1')

    def __init__(self, step, policy):
        from smilepcb.routing_quality import current_quality
        self.grid_step = step
        self.port_policy = policy
        self.quality = current_quality()
        self.added = []
        self.legs = []

    def clear(self, shape, layer, net):
        a, b, _ = shape
        if layer != 0:
            return False
        def contains(x, y, point):
            return ((point[0] - x[0]) * (y[1] - x[1]) == (point[1] - x[1]) * (y[0] - x[0]) and
                    all(min(x[i], y[i]) <= point[i] <= max(x[i], y[i]) for i in (0, 1)))
        edges = [((0, 0), (250000, 0)), ((250000, 0), (250000, 2000000))]
        return any(contains(x, y, a) and contains(x, y, b) for x, y in edges)

    def via_clear(self, *args):
        return False

    def expired(self):
        return False

    def add_path(self, points, layer, net):
        self.legs.append((layer, points))
        self.added.append(len(self.added))

    def add_via(self, *args):
        raise AssertionError('No via is legal in this regression')


@pytest.mark.parametrize('step', [250000, 125000, 50000])
def test_cap_clearance_atomic_access_reaches_the_same_legal_corridor(monkeypatch, step):
    from smilepcb.routing_quality import using_quality_profile
    grid, policy = load_production(monkeypatch)
    # Keep first-contact semantics: forward attachment is legal, source-side
    # shortcuts and interior-source contacts remain illegal.
    assert policy.accepts_segment((0, 0), (250000, 0), 0, 'signal')
    assert not policy.accepts_segment((0, 0), (-125000, 0), 0, 'signal')
    assert not policy.accepts_segment((-500000, 0), (-400000, 0), 0, 'signal')
    with using_quality_profile('turns'):
        router = CorridorRouter(step, policy)
        assert grid.search(router, (0, 0), (250000, 2000000), [0], [0], 'signal'), router.last_search
    assert router.legs[0][1][0] == (0, 0)
    assert router.legs[-1][1][-1] == (250000, 2000000)
    assert all(policy.accepts_segment(a, b, layer, 'signal', router.track_width)
               and router.clear((a, b, router.track_width), layer, 'signal')
               for layer, points in router.legs for a, b in zip(points, points[1:]))
