from smilepcb.keyboard_routing.polish_geometry import shortcut_candidates,shape_score,chains
from smilepcb.keyboard_routing.follow_geometry import foreign_chains,offset_path
from smilepcb.keyboard_routing.parallel_neighbors import ParallelNeighbors

def test_simple_initial_paths_can_remove_a_gratuitous_out_and_back_jog():
 old=[(0,0),(2000000,0),(4000000,2000000),(6000000,2000000),(8000000,0),(10000000,0)]
 assert min(shortcut_candidates(old),key=lambda p:shape_score(p,2000000))==[(0,0),(10000000,0)]

def test_foreign_follow_candidates_ignore_self_and_serialization_splits():
 row=lambda n,a,b:dict(net=n,layer=0,width=150000,start=a,end=b)
 base=[row('arbitrary-r',(0,0),(10000000,0)),row('arbitrary-c',(0,1000000),(10000000,1000000))]
 split=[base[0],row('arbitrary-c',(0,1000000),(5000000,1000000)),row('arbitrary-c',(5000000,1000000),(10000000,1000000))]
 eligible={'arbitrary-r','arbitrary-c'}
 a=foreign_chains(base,'arbitrary-r',eligible);b=foreign_chains(split,'arbitrary-r',eligible)
 assert a==b and len(a)==1 and a[0]['net']=='arbitrary-c'
 for rows in (base,split):
  index=ParallelNeighbors(rows,eligible_nets=eligible,min_run_nm=5000000)
  assert index.coverage((0,0),(10000000,0),0,'arbitrary-r',150000)==10000000
  assert index.coverage((0,0),(10000000,0),0,'arbitrary-c',150000)==0

def test_normal_offset_lanes_keep_the_shared_45_degree_turn():
 assert offset_path([(0,0),(10000000,0),(15000000,5000000),(15000000,15000000)],500000)==[(0,500000),(9792893,500000),(14500000,5207107),(14500000,15000000)]
