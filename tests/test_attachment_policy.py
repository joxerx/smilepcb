import unittest
from smilepcb.keyboard_routing.attachment_policy import TerminalStyle,ranked_departures
from smilepcb.keyboard_routing.polish_geometry import octile


class TerminalHeading(unittest.TestCase):
    def style(self,rays=None,bounds=(-10,-10,10,10)):
        return TerminalStyle(bounds,{(0,0):{(1,0)}} if rays is None else rays)

    def test_target_right_ray_accepts_arrival_from_left_in_both_orders(self):
        style=self.style()
        self.assertTrue(style.accepts_path([(-2,0),(0,0)]))
        self.assertTrue(style.accepts_path([(0,0),(-2,0)]))

    def test_target_right_ray_rejects_vertical_and_overlapping_right(self):
        style=self.style()
        for point in ((0,2),(0,-2),(2,0)):
            self.assertFalse(style.accepts_path([point,(0,0)]))
            self.assertFalse(style.accepts_path([(0,0),point]))

    def test_source_left_ray_allows_right_departure(self):
        style=self.style({(0,0):{(-5,0)}})
        self.assertTrue(style.accepts_path([(0,0),(2,0)]))
        self.assertTrue(style.accepts_path([(2,0),(0,0)]))
        self.assertFalse(style.accepts_path([(0,0),(-2,0)]))

    def test_exact_135_degree_boundary_allowed_in_both_orders(self):
        style=self.style()
        for point in ((-2,2),(-2,-2)):
            self.assertTrue(style.accepts_path([(0,0),point]))
            self.assertTrue(style.accepts_path([point,(0,0)]))

    def test_both_declared_endpoints_enforced(self):
        style=self.style({(0,0):{(-1,0)},(2,0):{(1,0)}})
        self.assertTrue(style.accepts_path([(0,0),(2,0)]))
        self.assertTrue(style.accepts_path([(2,0),(0,0)]))
        style=self.style({(0,0):{(-1,0)},(2,0):{(-1,0)}})
        self.assertFalse(style.accepts_path([(0,0),(2,0)]))

    def test_bounds_include_boundary_and_reject_any_outside_point(self):
        style=self.style({})
        self.assertTrue(style.accepts_path([(-10,0),(10,0)]))
        self.assertFalse(style.accepts_path([(-11,0),(0,0)]))
        self.assertFalse(style.accepts_path([(0,0),(11,0)]))
        self.assertFalse(style.accepts_path([(-10,9),(-8,11),(-6,9)]))

    def test_existing_shape_policy_still_applies(self):
        style=self.style({})
        for path in ([],[(0,0)],[(0,0),(1,2)],[(0,0),(2,0),(2,2)]):
            self.assertFalse(style.accepts_path(path))

    def test_intermediate_mapped_point_does_not_restrict_grid_edges(self):
        self.assertTrue(self.style().accepts_path([(-2,0),(0,0),(2,0)]))

    def test_empty_and_multiple_rays_do_not_choose_arbitrary_heading(self):
        for rays in (set(),{(-1,0),(1,0)}):
            style=self.style({(0,0):rays})
            self.assertTrue(style.accepts_path([(0,0),(0,2)]))
            self.assertTrue(style.accepts_path([(0,2),(0,0)]))


class DepartureRanking(unittest.TestCase):
    def test_left_bank_delays_transverse_compaction(self):
        a=(0,0);z=(-2000000,500000)
        paths=ranked_departures(a,z,(-1,0))
        self.assertEqual(paths[0],[(0,0),(-1500000,0),z])
        self.assertEqual(ranked_departures(a,z),list(octile(a,z)))
        self.assertEqual({tuple(p) for p in paths},{tuple(p) for p in ranked_departures(a,z)})

    def test_rotated_top_bank_delays_transverse_compaction(self):
        a=(0,0);z=(500000,-2000000)
        paths=ranked_departures(a,z,(0,-1))
        self.assertEqual(paths[0],[(0,0),(0,-1500000),z])
        self.assertEqual({tuple(p) for p in paths},{tuple(p) for p in ranked_departures(a,z)})


if __name__=='__main__':unittest.main()
