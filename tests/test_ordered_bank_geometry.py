import pytest
from smilepcb.keyboard_routing.bank_geometry import escape_path,compatible_bank,terminal_origin,port_is_retired
from smilepcb.keyboard_routing.corridor_geometry import offset_path


def test_shared_escape_keeps_a_straight_neck_and_outward_heading():
 assert escape_path((0,0),0,1,4000000,500000,1000000)==[(0,0),(1000000,0),(1500000,500000),(4000000,500000)]
 assert escape_path((0,0),0,1,1000000,500000,1000000) is None


def test_neighboring_ports_in_one_bank_must_share_a_profile():
 a={'kind':'same_layer','bank':('mcu',0,1),'profile':(1000000,0)}
 assert compatible_bank(a,dict(a))
 assert not compatible_bank(a,{**a,'profile':(2000000,0)})
 assert compatible_bank(a,{**a,'bank':('mcu',1,1),'profile':(2000000,0)})


def test_same_face_port_stays_live_without_a_drill():
 p={'kind':'same_layer','terminal_id':'lead','via_id':None,'heading_origin_nm':(2,3),'via_nm':None}
 assert terminal_origin(p)==(2,3)
 assert not port_is_retired(p,{'lead'})
 assert port_is_retired(p,set())


def test_offset_elbows_keep_normal_pitch_through_both_45_degree_turns():
 path=[(0,0),(0,5000000),(5000000,10000000),(10000000,10000000)]
 assert offset_path(path,500000)==[(-500000,0),(-500000,5207107),(4792893,10500000),(10000000,10500000)]


@pytest.mark.parametrize('path',[
 [(0,0),(0,1000000),(0,0)],
 [(0,0),(1000000,2000000)],
])
def test_offset_rejects_reversals_and_non_octilinear_spines(path):
 with pytest.raises(ValueError):offset_path(path,500000)


def test_collinear_source_splits_do_not_change_lane_pitch_or_turn_events():
 assert offset_path([(0,0),(0,2000000),(0,5000000),(5000000,10000000),(10000000,10000000)],500000)==[(-500000,0),(-500000,5207107),(4792893,10500000),(10000000,10500000)]


@pytest.mark.parametrize('quarter',[0,1,2,3])
@pytest.mark.parametrize('turn',[1,-1])
def test_group_elbows_preserve_normal_pitch_in_each_cardinal_orientation(quarter,turn):
 from smilepcb.keyboard_routing.corridor_geometry import turn_bundle
 def rotate(q):
  x,y=q
  for _ in range(quarter):x,y=-y,x
  return x,y
 starts=[rotate(q) for q in [(0,0),(0,500000),(0,1000000)]]
 if turn==1:
  want=[[(0,0),(2000000,0),(3000000,1000000),(3000000,5000000)],[(0,500000),(1792893,500000),(2500000,1207107),(2500000,5000000)],[(0,1000000),(1585786,1000000),(2000000,1414214),(2000000,5000000)]]
 else:
  want=[[(0,0),(2000000,0),(3000000,-1000000),(3000000,-5000000)],[(0,500000),(2207107,500000),(3500000,-792893),(3500000,-5000000)],[(0,1000000),(2414214,1000000),(4000000,-585786),(4000000,-5000000)]]
 assert turn_bundle(starts,rotate((1,0)),turn,run=2000000,chamfer=1000000,reach=5000000)==[[rotate(q) for q in path] for path in want]


def test_finite_pad_pitch_transition_uses_symmetric_short_splays():
 from smilepcb.keyboard_routing.bank_geometry import pitch_transition
 assert pitch_transition([(0,0),(0,500000),(0,1000000)],0,1,2000000,1000000,350000)==[[(0,0),(1000000,0),(1150000,150000),(2000000,150000)],[(0,500000),(2000000,500000)],[(0,1000000),(1000000,1000000),(1150000,850000),(2000000,850000)]]
