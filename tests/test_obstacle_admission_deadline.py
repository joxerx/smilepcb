"""A bounded geometry seed may finish its started admission within group time."""
import unittest
from unittest.mock import patch
from smilepcb.keyboard_routing import obstacle_axis as axis


class AdmissionDeadline(unittest.TestCase):
    def search(self,finish,accepted,admission='omitted'):
        clock=[100.]
        def admit(edges):clock[0]=finish;return accepted
        kw={} if admission=='omitted' else dict(admission_deadline=admission)
        with patch.object(axis.time,'monotonic',side_effect=lambda:clock[0]):
            return axis.search_corridor((0,0),(2,0),start_heading=(1,0),layers=(0,2),preferred_layer=0,
                bounds_nm=(0,0,2,0),step_nm=1,minimum_run_nm=1,edge_clear=lambda *args:True,
                portal=None,deadline=101.,goal_layers=(0,),goal_accept=admit,**kw)

    def test_started_admission_can_finish_after_seed_before_group_deadline(self):
        self.assertTrue(self.search(102.,True,103.)['accepted'])

    def test_omitted_admission_deadline_preserves_old_seed_limit(self):
        self.assertFalse(self.search(102.,True)['accepted'])

    def test_started_admission_cannot_exceed_group_deadline(self):
        self.assertFalse(self.search(104.,True,103.)['accepted'])

    def test_rejected_admission_still_obeys_seed_deadline(self):
        result=self.search(102.,False,103.)
        self.assertFalse(result['accepted']);self.assertEqual(result['reason'],'deadline expired')

    def test_admission_deadline_must_be_finite_numeric_and_not_boolean(self):
        for value in (float('nan'),float('inf'),True,'103'):
            with self.assertRaises(ValueError):self.search(100.,True,value)


if __name__=='__main__':unittest.main()
