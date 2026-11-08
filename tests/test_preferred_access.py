from smilepcb.keyboard_routing.column_bridges import bridge_carrier
M=1000000

def test_smd_access_remains_on_its_face_but_the_long_run_prefers_the_other_face():
 route=bridge_carrier([(0,0),(0,10*M)],0,1,start_layers={1},end_layers={1},secondary_length_factor=8,via_cost_nm=500000,clear=lambda a,b,l:True,via_clear=lambda p:True,via_pair_clear=lambda a,b:abs(a[1]-b[1])>=M)
 assert route is not None and len(route['vias'])==2
 assert route['legs'][0][0]==route['legs'][-1][0]==1
 assert sum(abs(b[1]-a[1]) for face,q in route['legs'] if face==0 for a,b in zip(q,q[1:]))>=9*M

def test_existing_through_via_access_does_not_need_new_terminal_vias():
 def unwanted(p):raise AssertionError('Existing terminal access must be reused')
 route=bridge_carrier([(0,0),(0,10*M)],0,1,start_layers={0,1},end_layers={0,1},secondary_length_factor=8,clear=lambda a,b,l:True,via_clear=unwanted,via_pair_clear=lambda a,b:True)
 assert route['legs']==[(0,[(0,0),(0,10*M)])] and not route['vias']

def test_migration_can_bridge_a_preferred_layer_obstacle_and_keep_terminal_faces():
 clear=lambda a,b,l:l==1 or max(a[1],b[1])<4*M or min(a[1],b[1])>6*M
 route=bridge_carrier([(0,0),(0,10*M)],0,1,start_layers={1},end_layers={1},secondary_length_factor=8,via_cost_nm=500000,clear=clear,via_clear=lambda p:True,via_pair_clear=lambda a,b:abs(a[1]-b[1])>=M)
 assert route is not None and route['legs'][0][0]==route['legs'][-1][0]==1
 assert any(face==0 for face,q in route['legs'])
 assert all(clear(a,b,face) for face,q in route['legs'] for a,b in zip(q,q[1:]))
