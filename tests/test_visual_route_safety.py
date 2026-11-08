try:from smilepcb.keyboard_routing.visual_geometry import safe_route
except ImportError:safe_route=None
M=1000000

def test_local_45_degree_turns_do_not_make_a_self_crossing_route_safe():
 assert safe_route is not None
 loop=[(0,0),(5*M,0),(7*M,2*M),(7*M,7*M),(5*M,9*M),(0,9*M),(-2*M,7*M),(-2*M,2*M),(0,0),(10*M,0)]
 assert not safe_route({'legs':[(0,loop)],'vias':[]},150000,500000)

def test_valid_short_layer_bridge_remains_admissible():
 assert safe_route is not None
 route={'legs':[(0,[(0,0),(0,3*M)]),(1,[(0,3*M),(0,7*M)]),(0,[(0,7*M),(0,10*M)])],'vias':[(0,3*M),(0,7*M)]}
 assert safe_route(route,150000,500000)

def test_via_cannot_touch_a_nonincident_planned_segment():
 assert safe_route is not None
 route={'legs':[(0,[(0,0),(0,2*M)]),(1,[(0,2*M),(100000,2100000),(100000,3*M)])],'vias':[(0,2*M)]}
 assert not safe_route(route,150000,500000)
