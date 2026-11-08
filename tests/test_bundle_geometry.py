import importlib,math
import pytest
M=1000000
@pytest.fixture
def api():
 try:return importlib.import_module('smilepcb.keyboard_routing.bundle_geometry')
 except ImportError:pytest.fail('Joint bundle geometry is not implemented')
def group():
 a=[(2*M,5*M),(4*M,5*M),(6*M,3*M),(8*M,3*M),(12*M,7*M),(14*M,7*M),(16*M,5*M),(18*M,5*M)]
 return [dict(net=n,layer=0,width=150000,path=[(x,y+dy) for x,y in a],ids=[n]) for n,dy in [('arbitrary-a',0),('arbitrary-b',M)]]
def test_joint_proposals_can_straighten_mutually_blocking_chains(api):
 g=group();proposals=api.bundle_candidates(g,clearance_nm=150000)
 assert proposals and proposals[0]['score']<api.bundle_score([x['path'] for x in g],[x['width'] for x in g],clearance_nm=150000)
 assert any(all(len(p)==2 for p in q['paths']) for q in proposals)
def test_candidate_keeps_each_net_on_its_own_original_terminals(api):
 g=group()
 for q in api.bundle_candidates(g,clearance_nm=150000):
  for old,new in zip(g,q['paths']):assert new[0]==old['path'][0] and new[-1]==old['path'][-1]
  assert api.paths_clear(q['paths'],[x['width'] for x in g],150000)
def test_same_net_does_not_get_a_self_parallel_bundle(api):
 g=group();g[1]['net']=g[0]['net']
 with pytest.raises(ValueError):api.bundle_candidates(g,clearance_nm=150000)
def test_group_crossings_and_insufficient_edge_gaps_are_rejected(api):
 assert not api.paths_clear([[(0,0),(10*M,0)],[(5*M,-M),(5*M,M)]],[150000,150000],150000)
 assert not api.paths_clear([[(0,0),(10*M,0)],[(0,299999),(10*M,299999)]],[150000,150000],150000)
 assert api.paths_clear([[(0,0),(10*M,0)],[(0,300000),(10*M,300000)]],[150000,150000],150000)
def test_score_prefers_uniform_neighbor_spacing_to_unnecessary_spreading(api):
 straight=[[(0,0),(10*M,0)],[(0,M),(10*M,M)]]
 spread=[straight[0],[(0,M),(2*M,M),(4*M,3*M),(6*M,3*M),(8*M,M),(10*M,M)]]
 assert api.bundle_score(straight,[150000]*2,clearance_nm=150000)<api.bundle_score(spread,[150000]*2,clearance_nm=150000)
def test_result_is_invariant_to_translation_and_quarter_turn(api):
 g=group();transform=lambda p:(-p[1]+17*M,p[0]-9*M)
 moved=[dict(x,path=[transform(p) for p in x['path']]) for x in g]
 a=api.bundle_candidates(g,clearance_nm=150000);b=api.bundle_candidates(moved,clearance_nm=150000)
 assert a and b and math.isclose(a[0]['score'],b[0]['score'],abs_tol=2)
def test_discovery_groups_different_real_neighbors_and_does_not_group_a_net_with_itself(api):
 g=group();extra=dict(g[0],net='distant',ids=['distant'],path=[(x,y+50*M) for x,y in g[0]['path']]);groups=api.neighbor_groups([*g,extra])
 assert groups and any({x['net'] for x in q}=={x['net'] for x in g} for q in groups)
 assert all(len({x['net'] for x in q})==len(q) and 'distant' not in {x['net'] for x in q} for q in groups)
@pytest.mark.parametrize('limit',[0,-1,True,1000])
def test_candidate_budget_is_explicit_and_bounded(api,limit):
 with pytest.raises(ValueError):api.bundle_candidates(group(),clearance_nm=150000,limit=limit)

def test_context_score_never_rewards_parallel_branches_of_the_same_net(api):
 paths=[[(0,0),(10*M,0)],[(0,M),(10*M,M)]]
 same=api.bundle_score(paths,[150000]*2,clearance_nm=150000,nets=['one','one'])
 foreign=api.bundle_score(paths,[150000]*2,clearance_nm=150000,nets=['one','two'])
 assert same>foreign and math.isclose(same,40000000,abs_tol=1)

def test_physical_admissibility_filters_options_before_the_beam_discards_detours(api):
 def clear(index,path):
  for a,b in zip(path,path[1:]):
   if min(a[0],b[0])<=7*M<=max(a[0],b[0]):
    if a[0]==b[0]:
     if max(a[1],b[1])>=4500000 and min(a[1],b[1])<=6500000:return False
    else:
     y=a[1]+(b[1]-a[1])*(7*M-a[0])/(b[0]-a[0])
     if 4500000<=y<=6500000:return False
  return True
 proposals=api.bundle_candidates(group(),clearance_nm=150000,path_admissible=clear)
 assert proposals and all(clear(i,path) for q in proposals for i,path in enumerate(q['paths']))
def test_neighbor_discovery_honors_an_expired_deadline(api):
 import time
 assert api.neighbor_groups(group(),deadline=time.monotonic()-1)==[]
def test_nonadjacent_copper_capsules_cannot_overlap_even_when_centerlines_do_not_cross(api):
 path=[(0,0),(8000000,0),(10000000,2000000),(10000000,8000000),(8000000,10000000),(2000000,10000000),(0,8000000),(0,2000000),(1900000,100000),(6000000,100000)]
 assert not api.paths_clear([path],[150000],150000)
def test_small_chamfer_remains_legal_despite_local_capsule_merging_at_the_corner(api):
 path=[(0,0),(1000000,0),(1100000,100000),(1100000,2000000)]
 assert api.paths_clear([path],[150000],150000)

def test_discovery_prioritizes_unnecessary_bends_over_an_already_straight_long_bus(api):
 ugly=group();neat=[dict(net='straight-'+str(i),layer=0,width=150000,path=[(0,50*M+i*M),(100*M,50*M+i*M)],ids=['straight-'+str(i)]) for i in range(2)]
 found=api.neighbor_groups([*ugly,*neat],limit=1)
 assert found and {q['net'] for q in found[0]}=={q['net'] for q in ugly}
def test_tiny_scalar_gain_does_not_justify_adding_a_dogleg(api):
 old=[[(0,0),(10*M,0),(20*M,10*M)],[(0,-M),(10*M,-M)]]
 new=[[(0,0),(150000,150000),(10150000,150000),(20*M,10*M)],old[1]]
 assert not api.quality_improves(old,new,10000000,9994600)
 assert not api.quality_improves(old,old,10000000,9994600)
 assert api.quality_improves([group()[0]['path'],group()[1]['path']], [[(2*M,5*M),(18*M,5*M)],[(2*M,6*M),(18*M,6*M)]],10000000,9000000)
