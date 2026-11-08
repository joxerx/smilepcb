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


# A guided shared spine is a caller input, not an automatically chosen corridor.
from smilepcb.keyboard_routing import pair_paths
from smilepcb.keyboard_routing.follow_geometry import direction
from smilepcb.keyboard_routing.polish_geometry import valid_shape
import math


def perpendicular_case():
    starts=[(98975444,92141210),(99508604,92141210)]
    ends=[(160000000,84000000),(160000000,83000000)]
    spine=[(99108734,92274500),(98500000,92883234),(98500000,93700000),
           (98900000,94100000),(147400000,94100000),(158000000,83500000),(159000000,83500000)]
    return starts,ends,spine


def guided(starts,ends,spine,**kwargs):
    return pair_paths.paired_spine_paths(starts,ends,spine,source_heading=kwargs.pop('source_heading',(-1,1)),
        arrival_heading=kwargs.pop('arrival_heading',(1,0)),target_lead_nm=kwargs.pop('target_lead_nm',1000000),
        min_body_segment_nm=kwargs.pop('min_body_segment_nm',400000),**kwargs)


def test_perpendicular_banks_have_exact_source_crop_forward_prefix_and_short_end_fans():
    starts,ends,spine=perpendicular_case()
    assert not list(paired_octile_paths(starts,ends))
    result=guided(starts,ends,spine)
    assert result is not None and len(result)==2
    for path,source,target in zip(result,starts,ends):
        assert path[0]==source and path[-1]==target and valid_shape(path)
        assert direction(path[0],path[1])==(-1,1)
        assert direction(path[-2],path[-1])==(1,0)
        assert all(type(v) is int for p in path for v in p)
        assert all(math.dist(a,b)>1 for a,b in zip(path,path[1:]))
    # The two preserved horizontal body carriers have the source's NORMAL
    # pitch, not the raw horizontal cut-boundary source spacing.
    horizontal=[[(a,b) for a,b in zip(path,path[1:]) if a[1]==b[1] and b[0]-a[0]>40000000] for path in result]
    assert len(horizontal[0])==len(horizontal[1])==1
    observed=abs(horizontal[0][0][0][1]-horizontal[1][0][0][1])
    expected=(starts[1][0]-starts[0][0])/math.sqrt(2)
    assert abs(observed-expected)<2


def test_guided_paths_are_equivariant_under_cardinal_rotations_reflections_and_translation():
    starts,ends,spine=perpendicular_case();original=guided(starts,ends,spine)
    assert original
    for turns in range(4):
        for reflected in (False,True):
            def vector(p):
                x,y=turn(p,turns)
                return (-x if reflected else x,y)
            def point(p):
                x,y=vector(p)
                return x+70000001,y-50000003
            result=guided(list(map(point,starts)),list(map(point,ends)),list(map(point,spine)),
                source_heading=vector((-1,1)),arrival_heading=vector((1,0)))
            assert result is not None
            # Integer miter rounding may differ by one nm under reflection.
            assert [len(p) for p in result]==[len(p) for p in original]
            assert all(math.dist(a,point(b))<=2 for path,old in zip(result,original) for a,b in zip(path,old))
            assert all(path[0]==point(source) and path[-1]==point(target)
                       for path,source,target in zip(result,starts,ends))


def test_guided_pair_fails_closed_on_inverted_order_impossible_fan_or_short_shared_run():
    starts,ends,spine=perpendicular_case()
    assert guided(starts,ends[::-1],spine) is None
    assert guided(starts,ends,spine,target_lead_nm=100000) is None
    assert guided(starts,ends,spine,min_body_segment_nm=500000) is None
    assert guided(starts,ends,spine,source_heading=(1,-1)) is None
    assert guided(starts,ends,spine,arrival_heading=(-1,0)) is None


def test_guided_pair_has_no_native_length_policy_waiver():
    starts,ends,spine=perpendicular_case();result=guided(starts,ends,spine)
    assert result
    lengths=[sum(math.dist(a,b) for a,b in zip(path,path[1:])) for path in result]
    # This supplied geometry needs the unchanged downstream skew gate/tuning.
    assert abs(lengths[0]-lengths[1])>500000
