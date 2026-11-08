"""Preferred pitch weights must not exclude tighter physically legal bundles."""
import unittest
from smilepcb.keyboard_routing.visual_geometry import UniformNeighbors
from smilepcb.keyboard_routing.role_quality import role_coverage
M=1000000


def segment(net,y):return dict(start=(0,y),end=(4*M,y),net=net,layer=0,width=150000,**{'class':'PCB_TRACK'})


class MetricV2(unittest.TestCase):
    def index(self,**kw):
        return UniformNeighbors([segment('B',0)],eligible_nets={'A','B'},min_edge_gap=0,
                                max_edge_gap=M,min_run_nm=2*M,preferred_edge_gap_nm=198000,**kw)

    def test_tight_and_preferred_gaps_receive_equal_full_weight(self):
        index=self.index()
        for gap in (176000,198000):
            self.assertEqual(index.coverage((0,150000+gap),(4*M,150000+gap),0,'A',150000),4*M)

    def test_above_preferred_gap_attenuates_using_original_preferred_scale(self):
        actual=self.index().coverage((0,370000),(4*M,370000),0,'A',150000)
        self.assertAlmostEqual(actual,4*M/(1+22000/348000))
        self.assertGreater(actual,0)
        self.assertLessEqual(actual,4*M)

    def test_self_and_negative_edge_gaps_do_not_count(self):
        index=self.index()
        self.assertEqual(index.coverage((0,326000),(4*M,326000),0,'B',150000),0)
        self.assertEqual(index.coverage((0,100000),(4*M,100000),0,'A',150000),0)

    def test_default_minimum_remains_legacy_eligibility_and_preference(self):
        index=UniformNeighbors([segment('B',0)],eligible_nets={'A','B'},min_edge_gap=198000,max_edge_gap=M,min_run_nm=2*M)
        self.assertEqual(index.coverage((0,326000),(4*M,326000),0,'A',150000),0)
        self.assertEqual(index.coverage((0,348000),(4*M,348000),0,'A',150000),4*M)
        self.assertAlmostEqual(index.coverage((0,370000),(4*M,370000),0,'A',150000),4*M/(1+22000/348000))

    def test_role_metric_counts_both_tight_pair_members_without_role_leakage(self):
        tracks={'a':segment('A',0),'b':segment('B',326000)}
        result=role_coverage(tracks,{'columns':{'A','B'},'rows':{'C','D'}},(-M,-M,5*M,2*M))
        self.assertEqual(result,{'columns':8*M,'rows':0})

    def test_role_coverage_preserves_preferred_pair_score(self):
        roles={'columns':{'A','B'}};bounds=(-M,-M,5*M,2*M)
        tight=role_coverage({'a':segment('A',0),'b':segment('B',326000)},roles,bounds)
        preferred=role_coverage({'a':segment('A',0),'b':segment('B',348000)},roles,bounds)
        self.assertEqual(tight,preferred)
        self.assertEqual(preferred['columns'],8*M)


if __name__=='__main__':unittest.main()
