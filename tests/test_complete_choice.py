"""Observable complete-candidate selection and immutable timeout fallback."""
import importlib,importlib.util,math,unittest

def ops(y=0,net='A'):
 return [dict(kind='track',net=net,layer=0,width=150,start=(0,y),end=(1000,y))]

class CompleteCandidateTests(unittest.TestCase):
 def make(self):
  name='smilepcb.keyboard_routing.complete_choice'
  self.assertIsNotNone(importlib.util.find_spec(name),'Complete candidate selection is missing')
  self.api=importlib.import_module(name);self.now=[0.]
  return self.api.CompleteChoice(deadline=10.,clock=lambda:self.now[0])
 def test_later_better_complete_route_replaces_first(self):
  c=self.make();c.consider(ops(),[{'end':(1,0)}],score=3,eligible=True)
  c.consider(ops(10),[{'end':(2,0)}],score=9,eligible=True)
  self.assertEqual(c.best['score'],9);self.assertEqual(c.best['operations'],ops(10))
 def test_lower_or_tied_scores_do_not_displace_incumbent(self):
  c=self.make();c.consider(ops(),[],score=9,eligible=True)
  c.consider(ops(10),[],score=3,eligible=True);c.consider(ops(20),[],score=9,eligible=True)
  self.assertEqual(c.best['operations'],ops());self.assertEqual(len(c.candidates),3)
 def test_emission_order_and_direction_do_not_fake_another_route(self):
  c=self.make();a=ops()+ops(20,'B');b=[dict(x,start=x['end'],end=x['start']) for x in reversed(a)]
  c.consider(a,[],score=3,eligible=True);c.consider(b,[],score=3,eligible=True)
  self.assertEqual(len(c.candidates),1);self.assertEqual(c.duplicates,1)
 def test_collinear_splits_do_not_fake_another_route(self):
  c=self.make();a=ops();b=[dict(a[0],end=(400,0)),dict(a[0],start=(400,0))]
  c.consider(a,[],score=3,eligible=True);c.consider(b,[],score=3,eligible=True)
  self.assertEqual(len(c.candidates),1)
 def test_non_octile_collinear_splits_are_not_extra_candidates(self):
  c=self.make();a=[dict(ops()[0],end=(1000,500))]
  b=[dict(a[0],end=(400,200)),dict(a[0],start=(400,200))]
  c.consider(a,[],score=3,eligible=True);c.consider(b,[],score=3,eligible=True)
  self.assertEqual(len(c.candidates),1)
 def test_candidate_and_best_are_isolated_from_later_working_state(self):
  c=self.make();a=ops();axis=[{'end':[1,0]}];c.consider(a,axis,score=3,eligible=True)
  a[0]['end']=(99,99);axis[0]['end'][0]=99
  saved=c.best;saved['operations'][0]['end']=(55,55)
  self.assertEqual(c.best['operations'],ops());self.assertEqual(c.best['axis'],[{'end':[1,0]}])
 def test_ineligible_high_score_is_recorded_but_never_selected(self):
  c=self.make();c.consider(ops(),[],score=3,eligible=True)
  c.consider(ops(10),[],score=30,eligible=False)
  self.assertEqual(c.best['score'],3);self.assertEqual(len(c.candidates),2)
 def test_late_completion_does_not_replace_good_incumbent(self):
  c=self.make();c.consider(ops(),[],score=3,eligible=True);self.now[0]=10.
  c.consider(ops(10),[],score=30,eligible=True)
  self.assertEqual(c.best['score'],3);self.assertEqual(len(c.candidates),1);self.assertEqual(c.late,1)
 def test_nonfinite_or_boolean_scores_are_rejected(self):
  c=self.make()
  for value in (math.nan,math.inf,True):
   with self.assertRaises(ValueError):c.consider(ops(),[],score=value,eligible=True)
  self.assertIsNone(c.best)
 def test_only_ineligible_completed_routes_leave_no_winner(self):
  c=self.make();c.consider(ops(),[],score=30,eligible=False);self.assertIsNone(c.best)
 def test_same_geometry_cannot_acquire_inconsistent_score(self):
  c=self.make();c.consider(ops(),[],score=3,eligible=True)
  with self.assertRaises(ValueError):c.consider(ops(),[],score=30,eligible=True)

if __name__=='__main__':unittest.main()
