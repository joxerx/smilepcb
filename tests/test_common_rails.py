"""Shared corridor proposals are independent of net names and board fixtures."""
import time
from smilepcb.keyboard_routing.common_rails import common_rail_spines
from smilepcb.keyboard_routing.follow_geometry import direction
from smilepcb.keyboard_routing.polish_geometry import valid_shape
M=1_000_000


def case(**kw):
    return common_rail_spines((0,0),(60*M,-10*M),source_heading=(-1,1),arrival_heading=(1,0),
        bounds_nm=(-4*M,-14*M,64*M,4*M),obstacle_bounds_nm=[(28*M,-4*M,34*M,2*M)],
        envelope_radius_nm=600000,**kw)


def test_perpendicular_banks_have_a_long_common_rail_and_correct_terminal_headings():
    paths=case();assert paths
    for path in paths:
        assert path[0]==(0,0) and path[-1]==(60*M,-10*M)
        assert direction(*path[:2])==(-1,1) and direction(*path[-2:])==(1,0)
        assert valid_shape(path)
    assert any(any(a[1]==b[1] and b[0]-a[0]>40*M for a,b in zip(p,p[1:])) for p in paths)


def test_translation_preserves_every_candidate_without_grid_phase_artifacts():
    offset=(1234567,-2345678);move=lambda p:tuple(p[i]+offset[i] for i in (0,1))
    base=case();a,z=move((0,0)),move((60*M,-10*M));lo=move((-4*M,-14*M));hi=move((64*M,4*M));o0=move((28*M,-4*M));o1=move((34*M,2*M))
    shifted=common_rail_spines(a,z,source_heading=(-1,1),arrival_heading=(1,0),bounds_nm=(*lo,*hi),obstacle_bounds_nm=[(*o0,*o1)],envelope_radius_nm=600000)
    assert {tuple(move(p) for p in path) for path in base}=={tuple(p) for p in shifted}


def test_cardinal_rotation_and_reflection_preserve_geometry():
    for transform in (lambda p:(-p[1],p[0]),lambda p:(p[0],-p[1])):
        def box(b):
            points=[transform((x,y)) for x in (b[0],b[2]) for y in (b[1],b[3])]
            return (min(x for x,y in points),min(y for x,y in points),max(x for x,y in points),max(y for x,y in points))
        paths=common_rail_spines(transform((0,0)),transform((60*M,-10*M)),source_heading=transform((-1,1)),arrival_heading=transform((1,0)),bounds_nm=box((-4*M,-14*M,64*M,4*M)),obstacle_bounds_nm=[box((28*M,-4*M,34*M,2*M))],envelope_radius_nm=600000)
        assert {tuple(transform(p) for p in path) for path in case()}=={tuple(p) for p in paths}


def test_one_absolute_deadline_and_finite_candidate_limit():
    assert case(deadline=time.monotonic()-1)==[]
    assert 0<len(case(max_candidates=1))<=1


def test_terminal_heading_comes_from_the_physical_bank_and_body():
    from smilepcb.keyboard_routing.common_rails import terminal_heading
    assert terminal_heading([(0,0),(200000,200000)],(M,-2*M),departure=True)==(-1,1)
    assert terminal_heading([(60*M,-10*M),(60*M,-11*M)],(62*M,-10*M),departure=False)==(1,0)
    assert terminal_heading([(0,0),(M,0),(M,M)],(0,-M),departure=True) is None
