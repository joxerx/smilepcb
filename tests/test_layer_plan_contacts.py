"""Check the finite copper of a plan before committing its same-net legs."""
from smilepcb.keyboard_routing.layer_plan import simple_layer_plan
M=1000000


def test_same_face_near_crossing_is_rejected_even_without_axis_intersection():
 a=(0,0);u=(20*M,20*M);v=(10*M,30*M);b=(10*M,101*M//10)
 assert not simple_layer_plan([(0,[a,u]),(2,[u,v]),(0,[v,b])],[u,v],width=150000,via_width=500000)


def test_sharp_projected_turn_at_layer_change_is_allowed():
 a=(0,0);u=(20*M,20*M);v=(10*M,30*M);b=(10*M,12*M)
 assert simple_layer_plan([(0,[a,u]),(2,[u,v]),(0,[v,b])],[u,v],width=150000,via_width=500000)


def test_via_annulus_must_not_touch_nonincident_copper():
 a=(0,0);u=(20*M,20*M);v=(10*M,30*M);b=(199*M//10,201*M//10)
 assert not simple_layer_plan([(0,[a,u]),(2,[u,v]),(0,[v,b])],[u,v],width=150000,via_width=500000)


def test_layers_are_checked_separately_and_order_must_be_continuous():
 a=(0,0);u=(0,5*M);v=(5*M,0);b=(5*M,5*M)
 assert simple_layer_plan([(0,[a,u]),(2,[u,v]),(0,[v,b])],[u,v],width=150000,via_width=500000)
 assert not simple_layer_plan([(0,[a,u]),(2,[v,b])],[u],width=150000,via_width=500000)
