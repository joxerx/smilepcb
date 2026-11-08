"""Production visibility attachment keeps reconstructed-plan admission intact."""
import ast
import itertools
import math
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from smilepcb.keyboard_routing import exact_visibility
from smilepcb.keyboard_routing.joint_repair import search_paths
from smilepcb.keyboard_routing.polish_geometry import valid_shape


def extracted(name, file, namespace):
    source = Path(__file__).resolve().parents[1] / 'smilepcb/keyboard_routing' / file
    node = next(node for node in ast.parse(source.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[name]


def geometry(paths, points):
    native = SimpleNamespace(F_Cu=0, B_Cu=2, SHAPE_SEGMENT=lambda a, b, width: (a, b, width),
                             SHAPE_CIRCLE=lambda point, radius: (point, radius))
    namespace = dict(p=native, itertools=itertools, time=time, math=math, vec=lambda point: point,
                     visibility_points=lambda *args, **kwargs: points,
                     _obstacles=lambda *args: [], paths=paths, search_paths=search_paths)
    return extracted('_plan_geometry', 'joint_repair_native.py', namespace)


class Region:
    bounds = (-100, -100, 100, 100)
    def __init__(self, points, padding):
        pass
    def accepts_path(self, path):
        return True


class World:
    track_width = 150000
    via_width = 500000
    drill = 200000
    port_policy = None
    path_policy = SimpleNamespace(accepts_path=valid_shape)
    def __init__(self):
        self.deadline = time.monotonic() + 10
        self.operations = []
        self.grid_plan_admission = lambda legs, vias, net: len(legs) == 1
    def collisions(self, *args):
        return set()
    def via_clear(self, *args):
        return False
    def via_pair_clear(self, *args):
        return True
    def clear(self, *args):
        return True
    def add_path(self, path, face, net):
        self.operations.append((face, path, net))
    def add_via(self, point, net):
        raise AssertionError('No via is available in this fixture')


def test_normalize_joins_same_layer_edges_and_orders_transition_vias():
    plan = {'legs': [(0, [(0, 0), (1, 0)]), (0, [(1, 0), (2, 0)]),
                     (2, [(2, 0), (3, 1)])], 'vias': [(2, 0)]}
    assert exact_visibility.normalize(plan) == {'legs': [(0, [(0, 0), (2, 0)]),
                                                       (2, [(2, 0), (3, 1)])], 'vias': [(2, 0)]}
    for invalid in [dict(plan, vias=[]), dict(plan, vias=[(9, 9)]),
                    dict(plan, vias=[(2, 0), (2, 0)]),
                    dict(plan, legs=[(0, [(0, 0), (1, 0)]), (2, [(2, 0), (3, 0)])])]:
        assert exact_visibility.normalize(invalid) is None


def test_plan_geometry_filters_paths_without_changing_default_path_choice():
    a, b = (0, 0), (4, 2)
    candidates = [[a, (2, 0), b]] * 6 + [[a, (2, 2), b]]
    planner = geometry(lambda *args, **kwargs: candidates, [a, b])
    arguments = (World(), World(), [(a, 0)], [(b, 0)], 'n', Region([], 0), set(), time.monotonic() + 2)
    default, _ = planner(*arguments, max_results=1)
    filtered, _ = planner(*arguments, max_results=1, path_admission=lambda path: path[1] == (2, 2))
    assert default[0]['legs'] == [(0, candidates[0])]
    assert filtered[0]['legs'] == [(0, candidates[-1])]


def test_normalization_keeps_each_via_diameter_attached_to_its_position():
    a, first, second, z = (0, 0), (1, 0), (2, 0), (3, 0)
    plan = dict(legs=[(0, [a, first]), (2, [first, second]), (0, [second, z])],
                vias=[second, first], via_widths=[500000, 3200000])
    route = exact_visibility.normalize(plan)
    assert route['vias'] == [first, second]
    assert route['via_widths'] == [3200000, 500000]
    assert exact_visibility.normalize(dict(plan, via_widths=[3200000])) is None
    assert exact_visibility.normalize(dict(plan, via_widths=[3200000, None])) is None


def test_plan_geometry_forwards_turn_and_final_admission_to_real_search():
    a, bad, good, b = (0, 0), (2, 0), (1, 1), (2, 2)
    edges = {(a, bad), (bad, b), (a, good), (good, b)}
    planner = geometry(lambda x, y, **kwargs: [[x, y]] if (x, y) in edges else [], [a, bad, good, b])
    proposals, _ = planner(World(), World(), [(a, 0)], [(b, 0)], 'n', Region([], 0), set(), time.monotonic() + 2,
                           max_results=1, turn_admission=exact_visibility.smooth,
                           plan_admission=lambda plan: any(good in path for _, path in plan['legs']))
    assert proposals[0]['legs'] == [(0, [a, good]), (0, [good, b])]


def test_helper_normalizes_before_current_world_admission_and_emits_only_accepted_plan(monkeypatch):
    a, middle, b = (0, 0), (2, 0), (4, 0)
    edges = {(a, middle), (middle, b)}
    planner = geometry(lambda x, y, **kwargs: [[x, y]] if (x, y) in edges else [], [a, middle, b])
    native = SimpleNamespace(FromMM=lambda value: round(value * 1000000),
                             SHAPE_SEGMENT=lambda x, y, width: (x, y, width))
    validator = extracted('valid_plan', 'grid_search.py', dict(p=native, vec=lambda point: point,
                          distance=math.dist, planned_vias_clear=lambda *args: True))
    grid = SimpleNamespace(valid_plan=validator)
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.grid_search', grid)
    emitter = extracted('_emit', 'joint_repair_native.py', dict(__package__='smilepcb.keyboard_routing'))
    joint = SimpleNamespace(_Region=Region, _plan_geometry=planner, _emit=emitter)
    monkeypatch.setitem(sys.modules, 'smilepcb.keyboard_routing.joint_repair_native', joint)
    world = World()
    assert exact_visibility.search(world, a, b, [0], [0], 'n')
    assert world.operations == [(0, [a, middle], 'n'), (0, [middle, b], 'n')]
    rejected = World()
    rejected.grid_plan_admission = lambda *args: False
    assert not exact_visibility.search(rejected, a, b, [0], [0], 'n')
    assert rejected.operations == []
