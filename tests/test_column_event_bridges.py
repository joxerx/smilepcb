"""Nonmonotone column alternatives change face at each otherwise sharp turn."""
from smilepcb.keyboard_routing.field_geometry import column_bridge_paths,horizontal_length
M=1000000

def test_event_bridge_has_no_horizontal_and_explicit_layer_changes_at_turns():
 a=(0,0);b=(14*M,19*M)
 proposals=column_bridge_paths(a,b,rail_ys=[-5*M,8*M,24*M])
 assert proposals
 for paths in proposals:
  assert len(paths)==3 and paths[0][0]==a and paths[-1][-1]==b
  assert paths[0][-1]==paths[1][0] and paths[1][-1]==paths[2][0]
  assert all(horizontal_length(path)==0 for path in paths)
  for path in paths:
   x,z=path;dx,dy=z[0]-x[0],z[1]-x[1]
   assert dy and (not dx or abs(dx)==abs(dy))
 assert any(paths[0][-1][1]<a[1] for paths in proposals)


def test_event_bridge_translation_and_finite_limit():
 a=(0,0);b=(14*M,19*M);shift=(1234567,-7654321);move=lambda p:(p[0]+shift[0],p[1]+shift[1])
 rows=column_bridge_paths(a,b,rail_ys=[-5*M,8*M,24*M],limit=3)
 moved=column_bridge_paths(move(a),move(b),rail_ys=[q+shift[1] for q in [-5*M,8*M,24*M]],limit=3)
 assert len(rows)<=3
 assert moved==[[[move(q) for q in leg] for leg in paths] for paths in rows]


def test_diagonal_terminal_escape_and_both_event_anchors_are_offered():
 a=(0,0);b=(14*M,19*M);events=[-5*M,8*M,24*M]
 rows=column_bridge_paths(a,b,rail_ys=events,limit=256)
 assert any(p[0][0][0]!=p[0][1][0] for p in rows)
 reverse=column_bridge_paths(b,a,rail_ys=events,limit=256)
 key=lambda ps:tuple(tuple(tuple(q) for q in leg) for leg in ps)
 assert {key(p) for p in rows}=={key([list(reversed(leg)) for leg in reversed(p)]) for p in reverse}


def test_dense_nearby_events_do_not_exclude_different_escape_corridors():
 a=(0,0);b=(14*M,19*M)
 events=[-q for q in range(1000,2*M,1000)]+[-4*M,-6*M,-10*M]
 rows=column_bridge_paths(a,b,rail_ys=events,limit=64)
 assert any(p[0][1][0]>a[0] and p[0][1][1]<=-4*M for p in rows)
