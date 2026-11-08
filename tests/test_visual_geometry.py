"""Behavioral tests for simple column paths and physically spaced foreign lanes."""
try:
 from smilepcb.keyboard_routing.visual_geometry import column_paths,duplicate_ids,UniformNeighbors,foreign_guides
except ImportError:column_paths=duplicate_ids=UniformNeighbors=foreign_guides=None
M=1000000

def test_column_turn_can_slide_without_a_horizontal_detour():
 assert column_paths is not None
 paths=column_paths((0,0),(4*M,10*M),step_nm=M)
 assert [(0,0),(0,3*M),(4*M,7*M),(4*M,10*M)] in paths
 assert all(q[0]==(0,0) and q[-1]==(4*M,10*M) for q in paths)
 assert all(a[1]!=b[1] and (a[0]==b[0] or abs(a[0]-b[0])==abs(a[1]-b[1])) for q in paths for a,b in zip(q,q[1:]))

def test_column_candidates_do_not_zigzag_to_fake_a_horizontal_feeder():
 assert column_paths is not None
 assert column_paths((0,0),(10*M,2*M))==[]
 assert column_paths((M,10*M),(M,0))==[[(M,10*M),(M,0)]]

def test_reversed_column_preserves_direction_and_endpoint_centres():
 assert column_paths is not None
 paths=column_paths((4*M,10*M),(0,0),step_nm=M)
 assert [(4*M,10*M),(4*M,7*M),(0,3*M),(0,0)] in paths
 assert all(a[1]>b[1] and a[0]>=b[0] for q in paths for a,b in zip(q,q[1:]))

def row(a,b,net='A',width=150000):return dict(class_='PCB_TRACK',start=a,end=b,width=width,layer=0,net=net,locked=False)
def track(a,b,**kw):
 r=row(a,b,**kw);r['class']=r.pop('class_');return r

def test_duplicate_pruning_keeps_locked_and_protected_representatives():
 assert duplicate_ids is not None
 rows={'free':track((0,0),(M,0)),'protected':track((M,0),(0,0)),'wide':track((0,0),(M,0),width=300000)}
 assert duplicate_ids(rows,{'protected'})==['free']
 rows['free']['locked']=True
 assert duplicate_ids(rows,{'protected'})==[]

def test_uniform_neighbour_reward_excludes_self_and_prefers_equal_tight_pitch():
 assert UniformNeighbors is not None
 segments=[dict(start=(0,0),end=(10*M,0),net='B',layer=0,width=150000)]
 n=UniformNeighbors(segments,eligible_nets={'A','B'},min_edge_gap=200000,max_edge_gap=M,min_run_nm=2*M)
 assert n.coverage((M,350000),(5*M,350000),0,'A',150000)==4*M
 assert 0<n.coverage((M,800000),(5*M,800000),0,'A',150000)<4*M
 assert n.coverage((M,350000),(5*M,350000),0,'B',150000)==0
 assert n.coverage((M,350000),(2*M,350000),0,'A',150000)==0

def test_continuous_foreign_run_is_merged_before_thresholding():
 assert UniformNeighbors is not None
 segments=[dict(start=(i*M,0),end=((i+1)*M,0),net='B',layer=0,width=150000) for i in range(4)]
 n=UniformNeighbors(segments,eligible_nets={'A','B'},min_edge_gap=200000,max_edge_gap=M,min_run_nm=2*M)
 assert n.coverage((0,350000),(4*M,350000),0,'A',150000)==4*M

def test_via_guides_come_from_foreign_geometry_at_the_physical_pitch():
 assert foreign_guides is not None
 rows=[dict(start=(0,0),end=(10*M,0),net='B',layer=0,width=150000)]
 assert (5*M,350000) in foreign_guides((5*M,2*M),rows,net='A',width=150000,gap_nm=200000)
 assert foreign_guides((5*M,2*M),rows,net='B',width=150000,gap_nm=200000)==[]
