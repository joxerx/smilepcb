"""A field role must reject shelves even when they are electrically legal."""
import pytest

@pytest.mark.parametrize('points,allowed',[
 ([(0,0),(0,10)],True),
 ([(0,0),(5,5),(5,10)],True),
 ([(5,10),(5,5),(0,0)],True),
 ([(0,0),(-5,5),(-5,10)],True),
 ([(0,0),(5,0),(10,5)],False),
 ([(0,0),(3,7)],False),
 ([(0,0),(0,0),(5,5)],True),
])
def test_column_candidates_obey_vertical_and_diagonal_geometry(points,allowed):
 from smilepcb.keyboard_routing.field_policy import FieldPathPolicy
 assert FieldPathPolicy('column').accepts_path(points) is allowed

def test_row_trunk_and_local_branch_have_separate_roles():
 from smilepcb.keyboard_routing.field_policy import FieldPathPolicy
 assert FieldPathPolicy('row').accepts_path([(0,1),(10,1)])
 assert not FieldPathPolicy('row').accepts_path([(0,1),(0,5),(10,5)])

def test_unknown_role_cannot_silently_disable_field_admission():
 from smilepcb.keyboard_routing.field_policy import FieldPathPolicy
 with pytest.raises(ValueError):FieldPathPolicy('feeder')
