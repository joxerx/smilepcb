try:from smilepcb.keyboard_routing.visual_geometry import feeder_paths
except ImportError:feeder_paths=None
M=1000000

def test_foreign_lane_can_be_joined_at_45_degrees_instead_of_a_right_angle():
 assert feeder_paths is not None
 foreign=[dict(net='other',layer=0,width=150000,path=[(0,0),(12*M,0)])]
 paths=feeder_paths([(0,3*M),(12*M,3*M)],foreign,net='query',layer=0,width=150000,gap_nm=200000)
 assert [(0,3*M),(2650000,350000),(9350000,350000),(12*M,3*M)] in paths

def test_feeder_does_not_follow_itself_or_a_different_physical_layer():
 assert feeder_paths is not None
 query=[(0,3*M),(12*M,3*M)]
 for net,layer in [('query',0),('other',1)]:
  assert feeder_paths(query,[dict(net=net,layer=layer,width=150000,path=[(0,0),(12*M,0)])],net='query',layer=0,width=150000,gap_nm=200000)==[]
