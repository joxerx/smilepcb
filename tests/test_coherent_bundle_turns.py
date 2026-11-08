"""Behavior tests for measured constant-pitch turns, not path text."""
import importlib.util,math,unittest
M=1000000

def rows(paths,faces=None,nets=None):
 out={}
 for i,path in enumerate(paths):
  for j,(a,z) in enumerate(zip(path,path[1:])):
   out[f'{i}:{j}']=dict(**{'class':'PCB_TRACK'},net=(nets or ['A','B','C'])[i],
      layer=(faces or [0]*len(paths))[i],width=150000,start=a,end=z,locked=False)
 return out

def pair(pitch=326000):
 # Offset intersection of y=p and y=x+sqrt(2)*p; true perpendicular pitch=p.
 q=round((1-math.sqrt(2))*pitch)
 return [[(-5*M,0),(0,0),(5*M,5*M)],
         [(-5*M,pitch),(q,pitch),(5*M+q,5*M+pitch)]]

class BundleTurns(unittest.TestCase):
 def api(self):
  name='smilepcb.keyboard_routing.bundle_turns'
  self.assertIsNotNone(importlib.util.find_spec(name),'Coherent turn geometry is missing')
  return __import__(name,fromlist=['bundle_turns'])
 def measure(self,tracks,bounds=(-10*M,-10*M,10*M,10*M)):
  return self.api().bundle_turns(tracks,{'A','B','C'},bounds)
 def test_constant_pitch_turn_is_one_maneuver_with_two_rewarded_members(self):
  m=self.measure(rows(pair()))
  self.assertEqual(m['raw_bends'],2)
  self.assertEqual(m['bundle_maneuvers'],1)
  self.assertAlmostEqual(m['shared_bend_equivalents'],2,places=5)
  self.assertEqual(m['isolated_bends'],0)
 def test_spacing_drift_does_not_earn_turn_credit(self):
  p=pair();p[1]=[(-5*M,350000),(500000,350000),(5500000,5350000)]
  self.assertEqual(self.measure(rows(p))['bundle_maneuvers'],0)
 def test_same_net_is_not_its_own_bundle(self):
  self.assertEqual(self.measure(rows(pair(),nets=['A','A']))['bundle_maneuvers'],0)
 def test_opposite_faces_are_not_a_bundle(self):
  self.assertEqual(self.measure(rows(pair(),faces=[0,2]))['bundle_maneuvers'],0)
 def test_short_shoulders_are_not_continuous_bundle_support(self):
  p=pair();p[0][0]=(-M,0)
  self.assertEqual(self.measure(rows(p))['bundle_maneuvers'],0)
 def test_collinear_splits_and_duplicates_do_not_change_credit(self):
  a=rows(pair());b=dict(a);b['dup']=dict(a['0:1']);b.pop('0:0')
  b.update({'split-'+k:v for k,v in rows([[(-5*M,0),(-3*M,0),(0,0)]],nets=['A']).items()})
  # Restore B entries overwritten by the helper's local IDs.
  for k,v in a.items():
   if k.startswith('1:'):b[k]=v
  self.assertEqual(self.measure(a),self.measure(b))
 def test_branch_vertex_is_not_rewarded_as_a_simple_turn(self):
  a=rows(pair());a['branch']=dict(a['0:0'],start=(0,0),end=(0,-3*M))
  self.assertEqual(self.measure(a)['bundle_maneuvers'],0)
 def test_branch_of_another_width_cannot_hide_behind_turn_credit(self):
  a=rows(pair());a['branch']=dict(a['0:0'],start=(0,0),end=(0,-3*M),width=200000)
  self.assertEqual(self.measure(a)['bundle_maneuvers'],0)
 def test_same_net_through_segment_suppresses_false_degree_two_corner(self):
  a=rows(pair());a['through']=dict(a['0:0'],start=(0,-3*M),end=(0,3*M))
  self.assertEqual(self.measure(a)['bundle_maneuvers'],0)
 def test_non_octilinear_branch_still_suppresses_turn_credit(self):
  a=rows(pair());a['branch']=dict(a['0:0'],start=(0,0),end=(M,-2*M))
  self.assertEqual(self.measure(a)['bundle_maneuvers'],0)
 def test_bounds_clip_support_without_inventing_a_corner(self):
  self.assertEqual(self.measure(rows(pair()),(-M,-M,10*M,10*M))['bundle_maneuvers'],0)
 def test_path_reversal_and_item_order_do_not_change_reward(self):
  a=rows(pair());b=rows([list(reversed(p)) for p in pair()])
  self.assertEqual(self.measure(a),self.measure(dict(reversed(list(b.items())))))
 def test_three_members_count_one_maneuver_not_three_pairs(self):
  p=pair();p.append(pair(652000)[1]);m=self.measure(rows(p))
  self.assertEqual(m['bundle_maneuvers'],1)
  self.assertEqual(m['shared_bend_equivalents'],3)
 def test_one_net_cannot_earn_multiple_member_credits_in_a_maneuver(self):
  p=pair();p.append(pair(652000)[1]);m=self.measure(rows(p,nets=['A','B','A']))
  self.assertEqual(m['bundle_maneuvers'],0)
 def test_same_bend_count_scores_better_when_turns_are_coordinated(self):
  api=self.api();a=rows(pair());p=pair();p[1]=[(x+4*M,y+3*M) for x,y in p[1]]
  b=rows(p);box=(-20*M,-20*M,20*M,20*M)
  x=api.bundle_objective(a,{'columns':{'A','B'}},box)['columns']
  y=api.bundle_objective(b,{'columns':{'A','B'}},box)['columns']
  self.assertGreater(x['turn_credit_nm'],0)
  self.assertGreater(x['score_nm'],y['score_nm'])
 def test_turn_credit_does_not_make_unnecessary_maneuvers_free(self):
  api=self.api();x=api.bundle_objective(rows(pair()),{'columns':{'A','B'}},(-10*M,-10*M,10*M,10*M))['columns']
  self.assertGreater(x['residual_turn_cost_nm'],0)

if __name__=='__main__':unittest.main()
