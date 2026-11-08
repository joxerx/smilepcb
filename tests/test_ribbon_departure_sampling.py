import importlib.util
from collections import Counter
from pathlib import Path
import unittest


SOURCE = Path(__file__).parents[1] / 'smilepcb' / 'keyboard_routing' / 'ribbon_port_candidates.py'
spec = importlib.util.spec_from_file_location('ribbon_port_candidates', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
port_pairs = module.port_pairs
departure_possible = module.departure_possible
# Frozen cap-only menu from SHA 82dd08eb91c1a9034e4714bba44bc1f6d5622dfc4138062ad5a8b7ae43f6a9d0.
BASELINE_PAIRS = [((1000000, 0), (11000000, 0)), ((1000000, 0), (9000000, 0)), ((1000000, 0), (5000000, 0)), ((1000000, 0), (3000000, 0)), ((2000000, 0), (12000000, 0)), ((2000000, 0), (10000000, 0)), ((2000000, 0), (6000000, 0)), ((2000000, 0), (4000000, 0)), ((1000000, 2000000), (11000000, 2000000)), ((1000000, -2000000), (11000000, -2000000)), ((1000000, 2000000), (9000000, 2000000)), ((1000000, -2000000), (9000000, -2000000)), ((1000000, 2000000), (5000000, 2000000)), ((1000000, -2000000), (5000000, -2000000)), ((1000000, 2000000), (3000000, 2000000)), ((1000000, -2000000), (3000000, -2000000))]

class RibbonDepartureSamplingTest(unittest.TestCase):
    def setUp(self):
        self.args = dict(source=(0, 0), targets=[(0, 12_000_000)], heading=(1, 0),
                         bounds=(-10_000_000, -10_000_000, 20_000_000, 10_000_000),
                         step_nm=1_000_000, lead_nm=1_000_000,
                         minimum_run_nm=2_000_000, free=lambda q: True)

    def test_primary_menu_is_unchanged_when_it_has_candidates(self):
        plain = BASELINE_PAIRS
        checked = port_pairs(**self.args, departure_free=lambda q: True)
        self.assertEqual(checked, plain)

    def test_empty_primary_menu_finds_first_free_lateral_ring(self):
        def free(q):
            return abs(q[1]) >= 3_000_000
        pairs = port_pairs(**dict(self.args, free=free))
        self.assertTrue(pairs)
        self.assertTrue(all(abs(a[1]) == 3_000_000 for a, _ in pairs))

    def test_departure_predicate_rejects_primary_and_finds_alternative(self):
        primary_starts = {a for a, _ in BASELINE_PAIRS}
        pairs = port_pairs(**self.args, departure_free=lambda q: abs(q[1]) > 2_000_000)
        self.assertTrue(pairs)
        starts = {a for a, _ in pairs}
        self.assertTrue(starts.isdisjoint(primary_starts))
        self.assertTrue(all(abs(a[1]) > 2_000_000 for a in starts))

    def test_departure_predicate_is_memoized_per_start(self):
        calls=Counter()
        pairs=port_pairs(**self.args, departure_free=lambda q: calls.update([q]) or True)
        self.assertTrue(pairs)
        self.assertTrue(calls)
        self.assertTrue(all(count==1 for count in calls.values()))

    def test_departure_predicate_must_be_callable(self):
        with self.assertRaises(ValueError):
            port_pairs(**self.args, departure_free=True)

    def test_all_blocked_menu_is_empty(self):
        self.assertEqual(port_pairs(**dict(self.args, free=lambda q: False)), [])

    def test_lateral_fallback_preserves_bounds_free_and_all_four_headings(self):
        for turns in range(4):
            def rotate(q):
                x,y=q
                for _ in range(turns):x,y=-y,x
                return (x,y)
            original=self.args['bounds']
            corners=[rotate((original[i],original[j])) for i in (0,2) for j in (1,3)]
            bounds=tuple([min(q[i] for q in corners) for i in (0,1)]+[max(q[i] for q in corners) for i in (0,1)])
            heading=rotate((1,0));normal=(-heading[1],heading[0]);seen=set()
            def free(q):
                seen.add(q)
                return abs(sum(q[i]*normal[i] for i in (0,1)))>=3_000_000
            args=dict(self.args,heading=heading,bounds=bounds,targets=[rotate(q) for q in self.args['targets']],free=free)
            pairs=port_pairs(**args)
            self.assertTrue(pairs)
            for a,z in pairs:
                self.assertEqual(abs(sum(a[i]*normal[i] for i in (0,1))),3_000_000)
                self.assertGreaterEqual(sum((z[i]-a[i])*heading[i] for i in (0,1)),2_000_000)
                for q in (a,z):
                    self.assertIn(q,seen)
                    self.assertTrue(free(q))
                    self.assertTrue(all(bounds[i]<=q[i]<=bounds[i+2] for i in (0,1)))

    def test_clear_second_layer_is_not_rejected_by_blocked_preferred_layer(self):
        self.assertTrue(departure_possible((0,0),(1,0),('F','B'),step=1,
                                           minimum_run=2,bounds=(-3,-3,3,3),
                                           edge_clear=lambda a,z,face:face=='B',
                                           portal=lambda point,face:None))

    def test_dead_start_stops_at_first_blocked_edge(self):
        calls = []
        okay = departure_possible((0, 0), (1, 0), ('F',), step=1,
                                  minimum_run=3, bounds=(-5, -5, 5, 5),
                                  edge_clear=lambda a, z, face: calls.append((a, z, face)) or z[0] < 1,
                                  portal=lambda point, face: False)
        self.assertFalse(okay)
        self.assertEqual(len(calls), 1)

    def test_portal_before_blocked_edge_preserves_departure(self):
        self.assertTrue(departure_possible((0, 0), (1, 0), ('F', 'B'), step=1,
                                           minimum_run=3, bounds=(-5, -5, 5, 5),
                                           edge_clear=lambda a, z, face: z[0] < 2,
                                           portal=lambda point, face: {'end': (2, 0), 'end_layer': 'B'} if point == (1, 0) and face == 'F' else None))

    def test_out_of_bounds_portal_is_not_a_valid_departure(self):
        self.assertFalse(departure_possible((0, 0), (1, 0), ('F',), step=1,
                                            minimum_run=3, bounds=(-2, -2, 2, 2),
                                            edge_clear=lambda a, z, face: False,
                                            portal=lambda point, face: {'end': (3, 0), 'end_layer': 'B'}))

    def test_portal_beyond_blocked_edge_is_never_queried(self):
        seen = []
        okay = departure_possible((0, 0), (1, 0), ('F', 'B'), step=1,
                                  minimum_run=3, bounds=(-5, -5, 5, 5),
                                  edge_clear=lambda a, z, face: z[0] < 1,
                                  portal=lambda point, face: seen.append((point, face)) or ({'end': (3, 0), 'end_layer': 'B'} if point[0] > 1 else None))
        self.assertFalse(okay)
        self.assertTrue(all(point[0] <= 0 for point, _ in seen))

    def test_directional_and_bounds_constraints(self):
        for heading in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            start=(0, 0)
            self.assertTrue(departure_possible(start, heading, ('F',), step=1,
                                               minimum_run=2, bounds=(-3, -3, 3, 3),
                                               edge_clear=lambda a, z, face: True,
                                               portal=lambda point, face: False))
        self.assertFalse(departure_possible((0, 0), (1, 0), ('F',), step=1,
                                            minimum_run=4, bounds=(-2, -2, 2, 2),
                                            edge_clear=lambda a, z, face: True,
                                            portal=lambda point, face: False))


if __name__ == '__main__':
    unittest.main()
