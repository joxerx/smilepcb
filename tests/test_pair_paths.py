"""Coupled octile joins retain lane ordering in each board orientation."""
from smilepcb.keyboard_routing.pair_paths import paired_octile_paths


def turn(point,n):
    x,y=point
    for _ in range(n):x,y=-y,x
    return x,y


def test_coupled_paths_are_simple_octile_in_every_orientation():
    for n in range(4):
        starts=[turn(q,n) for q in ((0,0),(0,533160))]
        ends=[turn(q,n) for q in ((6804493,1073057),(6804493,2073057))]
        choices=list(paired_octile_paths(starts,ends))
        assert 0<len(choices)<=256
        for pair in choices:
            for path,a,b in zip(pair,starts,ends):
                assert path[0]==a and path[-1]==b
                assert 2<=len(path)<=4
                for a,b in zip(path,path[1:]):
                    dx,dy=b[0]-a[0],b[1]-a[1]
                    assert (dx or dy) and (not dx or not dy or abs(dx)==abs(dy))


def test_reversed_lane_order_and_no_forward_room_are_rejected():
    assert not list(paired_octile_paths([(0,0),(0,1)],[(10,2),(10,1)]))
    assert not list(paired_octile_paths([(0,0),(0,1)],[(1,3),(1,4)]))


def test_geometry_does_not_invent_zero_length_joins():
    assert not list(paired_octile_paths([(0,0),(0,1)],[(0,0),(0,1)]))
