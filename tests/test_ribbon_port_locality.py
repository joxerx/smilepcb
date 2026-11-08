"""Near-source sampling remains local when ownership bounds cover a whole board."""
import math
import pytest
from smilepcb.keyboard_routing.ribbon_port_candidates import port_pairs

M=1000000
SOURCE=(70_214_002,60_178_003)
BOUNDS=(-37*M,-29*M,252*M,137*M)
STEP=250000
LEAD=M
HEADINGS=((1,0),(-1,0),(0,1),(0,-1))


def run(heading,*,bounds=BOUNDS,source=SOURCE,lead=LEAD):
    queries=[];starts=[]
    targets=[tuple(source[i]+heading[i]*distance for i in (0,1)) for distance in (12*M,14*M)]
    pairs=port_pairs(source,targets,heading,bounds,step_nm=STEP,lead_nm=lead,minimum_run_nm=2*M,
        free=lambda q:queries.append(q) or True,departure_free=lambda q:starts.append(q) or True)
    return pairs,queries,starts


def expected_nearest(heading,source=SOURCE,lead=LEAD):
    axis=0 if heading[0] else 1;sign=heading[axis]
    point=[round(value/STEP)*STEP for value in source]
    point[axis]=sign*((sign*source[axis]+lead+STEP-1)//STEP*STEP)
    return tuple(point)


@pytest.mark.parametrize('heading',HEADINGS)
def test_nearest_legal_lead_grid_point_is_physically_queried_and_returned(heading):
    pairs,queries,starts=run(heading)
    expected=expected_nearest(heading)
    assert expected in queries and expected in starts
    assert pairs[0][0]==expected


@pytest.mark.parametrize('heading',HEADINGS)
def test_wide_board_keeps_eight_dense_local_positions_and_fixed_distance_cap(heading):
    pairs,queries,starts=run(heading)
    projection=lambda q:sum((q[i]-SOURCE[i])*heading[i] for i in (0,1))
    first=projection(expected_nearest(heading))
    distances=sorted({projection(q) for q in starts})
    assert distances[:8]==[first+i*STEP for i in range(8)]
    assert len(distances)<=17
    extent=sum(abs(heading[i])*(BOUNDS[i+2]-BOUNDS[i]) for i in (0,1))
    nominal_last=max(math.ceil(LEAD/STEP)*STEP,extent//4//STEP*STEP)
    assert all(LEAD<=d<=nominal_last for d in distances)
    assert nominal_last-STEP<distances[-1]<=nominal_last
    assert all(BOUNDS[i]<=point[i]<=BOUNDS[i+2] for point in starts for i in (0,1))


@pytest.mark.parametrize('heading',HEADINGS)
def test_off_grid_required_lead_stays_inside_exact_bounds_and_distance_limit(heading):
    pairs,queries,starts=run(heading,lead=M+17251)
    assert pairs and pairs[0][0]==expected_nearest(heading,lead=M+17251)
    assert all(sum((q[i]-SOURCE[i])*heading[i] for i in (0,1))>=M+17251 for q in starts)
    assert all(BOUNDS[i]<=q[i]<=BOUNDS[i+2] for q in queries for i in (0,1))


def test_small_bounded_interval_contains_no_phantom_start_beyond_old_maximum():
    # There is no global-grid start between the exact lead and the old last.
    source=(17,23);bounds=(-800000,-1000000,800000,1000000)
    pairs,queries,starts=run((1,0),source=source,bounds=bounds,lead=500000)
    assert not pairs and not starts
    assert all(bounds[i]<=q[i]<=bounds[i+2] for q in queries for i in (0,1))


@pytest.mark.parametrize('heading',HEADINGS)
@pytest.mark.parametrize('source,bounds',[(SOURCE,BOUNDS),((-7_214_002,-6_178_003),(-252*M,-137*M,37*M,29*M))])
def test_forward_snapping_handles_signed_off_grid_origins(heading,source,bounds):
    pairs,queries,starts=run(heading,source=source,bounds=bounds,lead=M+17251)
    expected=expected_nearest(heading,source=source,lead=M+17251)
    assert expected in queries and pairs[0][0]==expected
    assert len(starts)<=17*3
    assert all(sum((q[i]-source[i])*heading[i] for i in (0,1))>=M+17251 for q in starts)


def test_native_filter_can_reject_nearest_start_and_next_local_grid_remains_available():
    heading=(-1,0);first=expected_nearest(heading)
    projection=lambda q:sum((q[i]-SOURCE[i])*heading[i] for i in (0,1))
    first_distance=projection(first);seen=[]
    pairs=port_pairs(SOURCE,[(SOURCE[0]-12*M,SOURCE[1])],heading,BOUNDS,step_nm=STEP,lead_nm=LEAD,
        minimum_run_nm=2*M,free=lambda q:seen.append(q) or projection(q)>first_distance)
    assert first in seen
    expected=(first[0]-STEP,first[1])
    assert expected in seen and pairs[0][0]==expected
