import math
from smilepcb.keyboard_routing.bundle_window import slice_legs, first_exit_distance

M=1000000

def test_cut_preserves_outside_layers_and_removes_only_internal_vias():
    legs=[(0,[(0,0),(4*M,0)]),(2,[(4*M,0),(8*M,0),(10*M,2*M)])]
    q=slice_legs(legs,2*M,8*M)
    assert q['source']==(2*M,0) and q['target']==(8*M,0)
    assert q['source_layer']==0 and q['target_layer']==2
    assert q['fixed_legs']==[(0,[(0,0),(2*M,0)]),(2,[(8*M,0),(10*M,2*M)])]
    assert q['fixed_vias']==[]
    assert q['prefix_legs']==[(0,[(0,0),(2*M,0)])]
    assert q['suffix_legs']==[(2,[(8*M,0),(10*M,2*M)])]
    assert q['body_legs']==[(0,[(2*M,0),(4*M,0)]),(2,[(4*M,0),(8*M,0)])]

def test_via_on_body_start_stays_as_a_fixed_source_contact():
    legs=[(0,[(0,0),(4*M,0)]),(2,[(4*M,0),(10*M,0)])]
    q=slice_legs(legs,4*M,6*M)
    assert q['fixed_vias']==[(4*M,0)]
    assert q['source_layer']==2 and q['target_layer']==2
    assert q['fixed_legs']==[(0,[(0,0),(4*M,0)]),(2,[(6*M,0),(10*M,0)])]

def test_window_exit_is_on_the_actual_diagonal_not_its_box_corner():
    d=first_exit_distance([[(0,0),(5*M,5*M),(9*M,5*M)]],[-3*M,-3*M,3*M,3*M])
    assert abs(d-3*M*math.sqrt(2))<1

def test_window_exit_stops_at_first_exit_even_when_route_returns():
    assert first_exit_distance([[(0,0),(5*M,0),(0,0)]],[-3*M,-3*M,3*M,3*M])==3*M
