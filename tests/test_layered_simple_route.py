from smilepcb.keyboard_routing.visual_geometry import safe_route
try:
 from smilepcb.keyboard_routing.layered_simple_route import safe_layered_route
except ImportError:
 safe_layered_route=safe_route
M=1_000_000

def shape():
 return {'legs':[(0,[(0,0),(6*M,0)]),(2,[(6*M,0),(8*M,2*M),(8*M,4*M),(6*M,6*M),(4*M,6*M),(2*M,4*M),(2*M,-2*M)])], 'vias':[(6*M,0)]}

def test_crossing_a_projection_on_the_other_face_does_not_form_an_electrical_loop():
 q=shape()
 assert not safe_route(q,150000,600000)
 assert safe_layered_route(q,150000,600000)

def test_crossing_on_the_same_face_is_still_rejected():
 q=shape();q['legs'].append((0,[(2*M,-2*M),(2*M,2*M)]));q['vias'].append((2*M,-2*M))
 assert not safe_layered_route(q,150000,600000)

def test_a_via_must_not_bridge_to_an_unrelated_part_of_its_own_route():
 q=shape();q['legs'][-1][1][-1]=(2*M,200000);q['vias'].append((2*M,200000));q['legs'].append((0,[(2*M,200000),(-2*M,200000)]))
 assert not safe_layered_route(q,150000,600000)

def test_layer_change_requires_the_exact_shared_endpoint_via():
 q=shape();q['vias']=[(6*M,100000)]
 assert not safe_layered_route(q,150000,600000)
