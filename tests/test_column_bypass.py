try:from smilepcb.keyboard_routing.visual_geometry import column_bypass_paths
except ImportError:column_bypass_paths=None
M=1000000

def test_column_can_leave_diagonally_away_from_a_hole_without_horizontal_legs():
 assert column_bypass_paths is not None
 paths=column_bypass_paths((3*M,3*M),(9*M,15*M),step_nm=500000)
 assert [(3*M,3*M),(M,5*M),(M,7*M),(9*M,15*M)] in paths
 assert all(a[1]<b[1] and (a[0]==b[0] or abs(a[0]-b[0])==b[1]-a[1]) for q in paths for a,b in zip(q,q[1:]))

def test_bypass_keeps_exact_terminals_and_does_not_invent_y_backtracking():
 assert column_bypass_paths is not None
 assert column_bypass_paths((0,0),(10*M,M))==[]
 paths=column_bypass_paths((9*M,15*M),(3*M,3*M),step_nm=500000)
 assert all(q[0]==(9*M,15*M) and q[-1]==(3*M,3*M) and all(a[1]>b[1] for a,b in zip(q,q[1:])) for q in paths)
