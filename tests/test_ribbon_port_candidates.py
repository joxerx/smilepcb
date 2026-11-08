import unittest
from smilepcb.keyboard_routing.ribbon_port_candidates import port_pairs
M=1000000


class PortCandidates(unittest.TestCase):
    def pairs(self,source=(0,0),targets=((20*M,0),(22*M,M)),heading=(1,0),bounds=(-24*M,-18*M,24*M,18*M),**kw):
        return port_pairs(source,targets,heading,bounds,step_nm=250000,lead_nm=M,minimum_run_nm=2*M,free=kw.pop('free',lambda q:True),**kw)

    def test_farther_start_when_near_mean_ports_blocked(self):
        pairs=self.pairs(free=lambda q:q[0]>=3*M)
        self.assertTrue(pairs)
        self.assertTrue(all(a[0]>=3*M for a,z in pairs))

    def test_near_far_targets_never_overshoot_nearest_forward_target(self):
        pairs=self.pairs(targets=((4*M,0),(20*M,0)))
        self.assertTrue(pairs)
        self.assertTrue(all(z[0]<=3500000 for a,z in pairs))

    def test_fully_blocked_returns_no_pairs(self):
        self.assertEqual(self.pairs(free=lambda q:False),[])

    def test_bounds_and_minimum_outward_run(self):
        pairs=self.pairs(bounds=(-M,-M,5*M,M))
        self.assertTrue(pairs)
        for a,z in pairs:
            self.assertGreaterEqual(z[0]-a[0],2*M)
            for q in (a,z):
                self.assertTrue(-M<=q[0]<=5*M and -M<=q[1]<=M)
        self.assertEqual(self.pairs(bounds=(-M,-M,2*M,M)),[])

    def test_bounded_distinct_deterministic_integer_output(self):
        pairs=self.pairs(limit=7)
        self.assertEqual(pairs,self.pairs(limit=7))
        self.assertLessEqual(len(pairs),7)
        self.assertEqual(len(pairs),len(set(pairs)))
        self.assertTrue(all(type(v)is int for pair in pairs for q in pair for v in q))

    def test_equal_shared_runs_start_at_the_nearest_free_bank_port(self):
        pairs=self.pairs()
        self.assertEqual(pairs[0][0][0],M)

    def test_targets_behind_bank_allow_short_prefix(self):
        pairs=self.pairs(targets=((-3*M,2*M),(-20*M,-M)))
        self.assertTrue(pairs)
        self.assertTrue(all(2*M<=z[0]-a[0]<=10*M for a,z in pairs))

    def test_opposite_heading_uses_nearest_forward_projection(self):
        pairs=self.pairs(targets=((-4*M,0),(-20*M,0)),heading=(-1,0))
        self.assertTrue(pairs)
        self.assertTrue(all(z[0]>=-3500000 and a[0]-z[0]>=2*M for a,z in pairs))

    def test_nondefault_minimum_run_and_two_mm_floor(self):
        for requested,expected in ((3*M,3*M),(M,2*M)):
            pairs=port_pairs((0,0),[(20*M,0)],(1,0),(-24*M,-18*M,24*M,18*M),
                             step_nm=250000,lead_nm=M,minimum_run_nm=requested,free=lambda q:True)
            self.assertTrue(pairs)
            self.assertTrue(all(z[0]-a[0]>=expected for a,z in pairs))

    def test_free_callback_checks_every_returned_endpoint(self):
        seen=set()
        def free(q):seen.add(q);return q[1]==0
        pairs=self.pairs(free=free)
        self.assertTrue(pairs)
        self.assertTrue(all(q in seen and q[1]==0 for pair in pairs for q in pair))



    def test_coherent_targets_allow_long_shared_run_with_fanout_release(self):
        pairs=self.pairs()
        self.assertTrue(pairs)
        start,goal=pairs[0]
        self.assertEqual(start,(M,0))
        self.assertGreater(goal[0]-start[0],10*M)
        self.assertEqual(goal[0],19*M)
        self.assertTrue(all(z[0]<=19*M for a,z in pairs))

    def test_incompatible_release_cap_defers_to_full_route_admission(self):
        # A seed is a proposal; the Native corridor and attachments must prove
        # feasibility. A heuristic cap cannot contradict the required lead/run.
        pairs=self.pairs(targets=((3*M,0),(20*M,0)))
        self.assertTrue(pairs)
        self.assertTrue(all(a[0]>=M and z[0]-a[0]>=2*M for a,z in pairs))
        self.assertEqual(self.pairs(targets=((3*M,0),(20*M,0)),free=lambda q:False),[])

    def test_blocked_long_goal_uses_backed_off_mean(self):
        pairs=self.pairs(free=lambda q:q[0]!=19*M)
        self.assertTrue(pairs)
        self.assertEqual(pairs[0][0],(M,0))
        self.assertEqual(pairs[0][1][0],18500000)

    def test_wide_normal_spread_release_is_bounded(self):
        pairs=self.pairs(targets=((20*M,-15*M),(22*M,15*M)))
        self.assertTrue(pairs)
        self.assertEqual(pairs[0][0],(M,0))
        self.assertEqual(pairs[0][1][0],14500000)
        self.assertGreater(pairs[0][1][0]-pairs[0][0][0],10*M)


if __name__=='__main__':unittest.main()
